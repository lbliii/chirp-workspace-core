"""Durable tenant identity and authorization repository for Workspace products."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from chirp.data import Database, DatabaseConnectionError, QueryError
from chirp.security import hash_password, verify_and_upgrade, verify_login

from ._tokens import issue_token, token_digest, tokens_equal
from .errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DatabaseUnavailableError,
    DuplicateInvitationError,
    InvitationConsumedError,
    InvitationError,
    InvitationExpiredError,
    InvitationRevokedError,
    LastOwnerError,
    NotFoundError,
    PasswordResetConsumedError,
    PasswordResetError,
    PasswordResetExpiredError,
    PasswordResetRevokedError,
    SchemaMismatchError,
    ValidationError,
)
from .models import (
    AuditEvent,
    BootstrapResult,
    Invitation,
    InvitationAcceptance,
    InvitationId,
    IssuedInvitation,
    IssuedPasswordReset,
    Membership,
    MembershipId,
    PasswordReset,
    PasswordResetId,
    Role,
    UserId,
    Workspace,
    WorkspaceId,
    WorkspacePrincipal,
    WorkspaceUser,
    new_id,
)
from .permissions import Permission, permissions_for_role

_T = TypeVar("_T")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class _UserRow:
    id: str
    email: str
    normalized_email: str
    password_hash: str
    display_name: str
    session_version: int
    is_active: int
    disabled_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class _WorkspaceRow:
    id: str
    slug: str
    name: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class _MembershipRow:
    id: str
    workspace_id: str
    user_id: str
    role: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class _InvitationRow:
    id: str
    workspace_id: str
    email: str
    normalized_email: str
    role: str
    token_hash: str
    expires_at: str
    accepted_at: str | None
    revoked_at: str | None
    invited_by: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _PasswordResetRow:
    id: str
    user_id: str
    token_hash: str
    expires_at: str
    used_at: str | None
    revoked_at: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class _BootstrapRow:
    id: int
    completed_at: str | None
    owner_user_id: str | None
    workspace_id: str | None


class WorkspaceRepository:
    """Single authority for local Workspace identities and tenant memberships."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = new_id,
        token_issuer: Callable[[], tuple[str, str]] = issue_token,
    ) -> None:
        self._db = database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._token_issuer = token_issuer

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            msg = "Workspace Core clock must return a timezone-aware datetime."
            raise RuntimeError(msg)
        return now.astimezone(UTC)

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _normalize_email(email: str) -> tuple[str, str]:
        display = email.strip()
        normalized = display.casefold()
        if not display or len(display) > 320 or normalized.count("@") != 1:
            raise ValidationError(
                "Workspace user email must be a valid address under 321 characters."
            )
        local, domain = normalized.split("@", 1)
        if not local or not domain or "." not in domain:
            raise ValidationError("Workspace user email must include a complete domain.")
        return display, normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12:
            raise ValidationError("Workspace passwords must contain at least 12 characters.")
        if len(password) > 1024:
            raise ValidationError("Workspace passwords must contain at most 1024 characters.")

    @staticmethod
    def _validate_display_name(display_name: str) -> str:
        value = display_name.strip()
        if not 1 <= len(value) <= 100:
            raise ValidationError("Workspace display names must contain 1 to 100 characters.")
        return value

    @staticmethod
    def _validate_workspace(slug: str, name: str) -> tuple[str, str]:
        clean_slug = slug.strip().casefold()
        clean_name = name.strip()
        if not _SLUG_RE.fullmatch(clean_slug):
            raise ValidationError(
                "Workspace slug must be 1 to 63 lowercase letters, digits, or single hyphens."
            )
        if not 1 <= len(clean_name) <= 120:
            raise ValidationError("Workspace name must contain 1 to 120 characters.")
        return clean_slug, clean_name

    @staticmethod
    def _schema_error(exc: QueryError) -> bool:
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in (
                "no such table",
                "no column named",
                "does not exist",
                "undefined table",
                "undefined column",
            )
        )

    async def _fetch_one(
        self,
        cls: type[_T],
        sql: str,
        *params: Any,
        surface: str,
    ) -> _T | None:
        try:
            return await self._db.fetch_one(cls, sql, *params)
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                f"Workspace database is unavailable while reading {surface}."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    f"Workspace schema cannot read {surface}; run the packaged Core migrations."
                ) from exc
            raise

    async def _fetch_val(self, sql: str, *params: Any, surface: str) -> Any:
        try:
            return await self._db.fetch_val(sql, *params)
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                f"Workspace database is unavailable while reading {surface}."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    f"Workspace schema cannot read {surface}; run the packaged Core migrations."
                ) from exc
            raise

    async def _execute(self, sql: str, *params: Any, surface: str) -> int:
        try:
            return await self._db.execute(sql, *params)
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                f"Workspace database is unavailable while updating {surface}."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    f"Workspace schema cannot update {surface}; run the packaged Core migrations."
                ) from exc
            raise

    @staticmethod
    def _user(row: _UserRow) -> WorkspaceUser:
        return WorkspaceUser(
            id=UserId(row.id),
            email=row.email,
            display_name=row.display_name,
            session_version=row.session_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            is_active=bool(row.is_active),
            disabled_at=row.disabled_at,
        )

    @staticmethod
    def _workspace(row: _WorkspaceRow) -> Workspace:
        return Workspace(
            id=WorkspaceId(row.id),
            slug=row.slug,
            name=row.name,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _membership(row: _MembershipRow) -> Membership:
        return Membership(
            id=MembershipId(row.id),
            workspace_id=WorkspaceId(row.workspace_id),
            user_id=UserId(row.user_id),
            role=Role(row.role),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _invitation(row: _InvitationRow) -> Invitation:
        return Invitation(
            id=InvitationId(row.id),
            workspace_id=WorkspaceId(row.workspace_id),
            email=row.email,
            role=Role(row.role),
            token_hash=row.token_hash,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
            revoked_at=row.revoked_at,
            invited_by=UserId(row.invited_by),
            created_at=row.created_at,
        )

    @staticmethod
    def _reset(row: _PasswordResetRow) -> PasswordReset:
        return PasswordReset(
            id=PasswordResetId(row.id),
            user_id=UserId(row.user_id),
            token_hash=row.token_hash,
            expires_at=row.expires_at,
            used_at=row.used_at,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
        )

    async def _user_row(self, user_id: str) -> _UserRow | None:
        return await self._fetch_one(
            _UserRow,
            "SELECT id, email, normalized_email, password_hash, display_name, "
            "session_version, is_active, disabled_at, created_at, updated_at "
            "FROM workspace_core_users WHERE id = ?",
            user_id,
            surface="user identity",
        )

    async def _workspace_row(self, workspace_id: str) -> _WorkspaceRow | None:
        return await self._fetch_one(
            _WorkspaceRow,
            "SELECT id, slug, name, created_at, updated_at "
            "FROM workspace_core_workspaces WHERE id = ?",
            workspace_id,
            surface="workspace tenant",
        )

    async def _membership_row(self, workspace_id: str, user_id: str) -> _MembershipRow | None:
        return await self._fetch_one(
            _MembershipRow,
            "SELECT id, workspace_id, user_id, role, created_at, updated_at "
            "FROM workspace_core_memberships WHERE workspace_id = ? AND user_id = ?",
            workspace_id,
            user_id,
            surface="workspace membership",
        )

    async def _event(
        self,
        *,
        workspace_id: str | None,
        actor_user_id: str | None,
        event_type: str,
        subject_type: str,
        subject_id: str,
        details: dict[str, str] | None = None,
    ) -> None:
        await self._execute(
            "INSERT INTO workspace_core_audit_events "
            "(id, workspace_id, actor_user_id, event_type, subject_type, subject_id, "
            "details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            self._id_factory(),
            workspace_id,
            actor_user_id,
            event_type,
            subject_type,
            subject_id,
            json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
            self._stamp(self._now()),
            surface="identity audit event",
        )

    async def create_user(
        self,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> WorkspaceUser:
        """Create one deployment-local identity with no tenant authority yet."""

        clean_email, normalized_email = self._normalize_email(email)
        clean_name = self._validate_display_name(display_name)
        self._validate_password(password)
        password_hash = hash_password(password)
        now = self._stamp(self._now())
        user_id = self._id_factory()
        try:
            await self._execute(
                "INSERT INTO workspace_core_users "
                "(id, email, normalized_email, password_hash, display_name, session_version, "
                "is_active, disabled_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 1, NULL, ?, ?)",
                user_id,
                clean_email,
                normalized_email,
                password_hash,
                clean_name,
                now,
                now,
                surface="user identity",
            )
        except QueryError as exc:
            if "unique" in str(exc).casefold():
                raise ConflictError(
                    "A Workspace user already exists for that email address."
                ) from exc
            raise
        row = await self._user_row(user_id)
        assert row is not None
        return self._user(row)

    async def load_user(self, user_id: str) -> WorkspaceUser | None:
        """Load an active secret-free user for Chirp's ``AuthConfig.load_user``."""

        row = await self._user_row(user_id)
        if row is None or not row.is_active:
            return None
        return self._user(row)

    async def authenticate(self, email: str, password: str) -> WorkspaceUser | None:
        """Verify local credentials with unknown-user timing resistance."""

        _, normalized_email = self._normalize_email(email)
        row = await self._fetch_one(
            _UserRow,
            "SELECT id, email, normalized_email, password_hash, display_name, "
            "session_version, is_active, disabled_at, created_at, updated_at "
            "FROM workspace_core_users WHERE normalized_email = ?",
            normalized_email,
            surface="login identity",
        )
        if not verify_login(password, row.password_hash if row and row.is_active else None):
            return None
        assert row is not None
        verified, upgraded = verify_and_upgrade(password, row.password_hash)
        if not verified:
            return None
        if upgraded is not None:
            now = self._stamp(self._now())
            await self._execute(
                "UPDATE workspace_core_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                upgraded,
                now,
                row.id,
                surface="password hash upgrade",
            )
            row = _UserRow(
                row.id,
                row.email,
                row.normalized_email,
                upgraded,
                row.display_name,
                row.session_version,
                row.is_active,
                row.disabled_at,
                row.created_at,
                now,
            )
        return self._user(row)

    async def create_workspace(
        self,
        *,
        owner_user_id: str,
        slug: str,
        name: str,
    ) -> tuple[Workspace, Membership]:
        """Create a tenant and its required first owner membership atomically."""

        clean_slug, clean_name = self._validate_workspace(slug, name)
        owner = await self.load_user(owner_user_id)
        if owner is None:
            raise NotFoundError("Workspace owner identity is missing or disabled.")
        now = self._stamp(self._now())
        workspace_id = self._id_factory()
        membership_id = self._id_factory()
        try:
            async with self._db.transaction():
                await self._execute(
                    "INSERT INTO workspace_core_workspaces "
                    "(id, slug, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    workspace_id,
                    clean_slug,
                    clean_name,
                    now,
                    now,
                    surface="workspace tenant",
                )
                await self._execute(
                    "INSERT INTO workspace_core_memberships "
                    "(id, workspace_id, user_id, role, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    membership_id,
                    workspace_id,
                    owner_user_id,
                    Role.OWNER.value,
                    now,
                    now,
                    surface="owner membership",
                )
                await self._event(
                    workspace_id=workspace_id,
                    actor_user_id=owner_user_id,
                    event_type="workspace.created",
                    subject_type="workspace",
                    subject_id=workspace_id,
                )
        except QueryError as exc:
            if "unique" in str(exc).casefold():
                raise ConflictError("A Workspace already uses that slug.") from exc
            raise
        workspace_row = await self._workspace_row(workspace_id)
        membership_row = await self._membership_row(workspace_id, owner_user_id)
        assert workspace_row is not None and membership_row is not None
        return self._workspace(workspace_row), self._membership(membership_row)

    async def bootstrap_first_owner(
        self,
        *,
        expected_setup_token: str,
        supplied_setup_token: str,
        email: str,
        display_name: str,
        password: str,
        workspace_slug: str,
        workspace_name: str,
    ) -> BootstrapResult:
        """Consume the deployment setup token and create the first tenant owner once."""

        if not expected_setup_token or not tokens_equal(expected_setup_token, supplied_setup_token):
            raise AuthenticationError("Workspace setup token is invalid.")
        clean_email, normalized_email = self._normalize_email(email)
        clean_name = self._validate_display_name(display_name)
        clean_slug, clean_workspace_name = self._validate_workspace(workspace_slug, workspace_name)
        self._validate_password(password)
        password_hash = hash_password(password)
        now = self._stamp(self._now())
        user_id = self._id_factory()
        workspace_id = self._id_factory()
        membership_id = self._id_factory()
        try:
            async with self._db.transaction():
                state = await self._fetch_one(
                    _BootstrapRow,
                    "SELECT id, completed_at, owner_user_id, workspace_id "
                    "FROM workspace_core_bootstrap WHERE id = 1",
                    surface="bootstrap state",
                )
                if state is None:
                    raise SchemaMismatchError(
                        "Workspace bootstrap row is missing; rerun the packaged Core migration."
                    )
                if state.completed_at is not None:
                    raise ConflictError("Workspace first-owner setup is already complete.")
                existing_users = int(
                    await self._fetch_val(
                        "SELECT COUNT(*) FROM workspace_core_users",
                        surface="bootstrap users",
                    )
                    or 0
                )
                existing_workspaces = int(
                    await self._fetch_val(
                        "SELECT COUNT(*) FROM workspace_core_workspaces",
                        surface="bootstrap workspaces",
                    )
                    or 0
                )
                if existing_users or existing_workspaces:
                    raise ConflictError(
                        "Workspace bootstrap state conflicts with existing identity data."
                    )
                await self._execute(
                    "INSERT INTO workspace_core_users "
                    "(id, email, normalized_email, password_hash, display_name, session_version, "
                    "is_active, disabled_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, 1, NULL, ?, ?)",
                    user_id,
                    clean_email,
                    normalized_email,
                    password_hash,
                    clean_name,
                    now,
                    now,
                    surface="bootstrap owner",
                )
                await self._execute(
                    "INSERT INTO workspace_core_workspaces "
                    "(id, slug, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    workspace_id,
                    clean_slug,
                    clean_workspace_name,
                    now,
                    now,
                    surface="bootstrap workspace",
                )
                await self._execute(
                    "INSERT INTO workspace_core_memberships "
                    "(id, workspace_id, user_id, role, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    membership_id,
                    workspace_id,
                    user_id,
                    Role.OWNER.value,
                    now,
                    now,
                    surface="bootstrap owner membership",
                )
                updated = await self._execute(
                    "UPDATE workspace_core_bootstrap SET completed_at = ?, owner_user_id = ?, "
                    "workspace_id = ? WHERE id = 1 AND completed_at IS NULL",
                    now,
                    user_id,
                    workspace_id,
                    surface="bootstrap completion",
                )
                if updated != 1:
                    raise ConflictError("Workspace first-owner setup was completed concurrently.")
                await self._event(
                    workspace_id=workspace_id,
                    actor_user_id=user_id,
                    event_type="workspace.bootstrap.completed",
                    subject_type="workspace",
                    subject_id=workspace_id,
                )
        except QueryError as exc:
            if "unique" in str(exc).casefold():
                raise ConflictError(
                    "Workspace first-owner setup conflicted with existing data."
                ) from exc
            raise
        user_row = await self._user_row(user_id)
        workspace_row = await self._workspace_row(workspace_id)
        membership_row = await self._membership_row(workspace_id, user_id)
        assert user_row is not None and workspace_row is not None and membership_row is not None
        return BootstrapResult(
            self._user(user_row),
            self._workspace(workspace_row),
            self._membership(membership_row),
        )

    async def principal(self, *, user_id: str, workspace_id: str) -> WorkspacePrincipal:
        """Resolve current tenant authority without leaking cross-tenant records."""

        user_row = await self._user_row(user_id)
        membership_row = await self._membership_row(workspace_id, user_id)
        workspace_row = await self._workspace_row(workspace_id)
        if (
            user_row is None
            or not user_row.is_active
            or membership_row is None
            or workspace_row is None
        ):
            raise AuthorizationError("Workspace membership does not authorize this tenant.")
        membership = self._membership(membership_row)
        return WorkspacePrincipal(
            user=self._user(user_row),
            workspace=self._workspace(workspace_row),
            membership=membership,
            permissions=frozenset(str(value) for value in permissions_for_role(membership.role)),
        )

    async def require_permissions(
        self,
        *,
        user_id: str,
        workspace_id: str,
        permissions: tuple[Permission | str, ...],
    ) -> WorkspacePrincipal:
        """Require every exact permission inside the selected workspace."""

        principal = await self.principal(user_id=user_id, workspace_id=workspace_id)
        required = frozenset(str(Permission(value)) for value in permissions)
        if not required.issubset(principal.permissions):
            raise AuthorizationError("Workspace membership does not grant the requested operation.")
        return principal

    async def _lock_workspace(self, workspace_id: str) -> None:
        updated = await self._execute(
            "UPDATE workspace_core_workspaces SET updated_at = updated_at WHERE id = ?",
            workspace_id,
            surface="workspace mutation lock",
        )
        if updated != 1:
            raise NotFoundError("Workspace tenant was not found.")

    async def issue_invitation(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        email: str,
        role: Role,
        lifetime: timedelta = timedelta(days=7),
    ) -> IssuedInvitation:
        """Issue one hashed, expiring invitation after tenant authorization."""

        actor = await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.MEMBERS_INVITE,),
        )
        if role is Role.OWNER:
            raise ValidationError("Workspace ownership cannot be granted by invitation.")
        if role is Role.ADMIN and actor.membership.role is not Role.OWNER:
            raise AuthorizationError("Only a Workspace owner may invite an administrator.")
        if lifetime <= timedelta(0) or lifetime > timedelta(days=30):
            raise ValidationError(
                "Workspace invitation lifetime must be between 1 second and 30 days."
            )
        clean_email, normalized_email = self._normalize_email(email)
        now_dt = self._now()
        now = self._stamp(now_dt)
        expires_at = self._stamp(now_dt + lifetime)
        token, digest = self._token_issuer()
        invitation_id = self._id_factory()
        try:
            async with self._db.transaction():
                await self._lock_workspace(workspace_id)
                active = await self._fetch_one(
                    _InvitationRow,
                    "SELECT id, workspace_id, email, normalized_email, role, token_hash, "
                    "expires_at, accepted_at, revoked_at, invited_by, created_at "
                    "FROM workspace_core_invitations "
                    "WHERE workspace_id = ? AND normalized_email = ? "
                    "AND accepted_at IS NULL AND revoked_at IS NULL",
                    workspace_id,
                    normalized_email,
                    surface="active invitation",
                )
                if active is not None and datetime.fromisoformat(active.expires_at) > now_dt:
                    raise DuplicateInvitationError(
                        "An active Workspace invitation already exists for that email address."
                    )
                if active is not None:
                    await self._execute(
                        "UPDATE workspace_core_invitations SET revoked_at = ? WHERE id = ?",
                        now,
                        active.id,
                        surface="expired invitation",
                    )
                await self._execute(
                    "INSERT INTO workspace_core_invitations "
                    "(id, workspace_id, email, normalized_email, role, token_hash, expires_at, "
                    "accepted_at, revoked_at, invited_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                    invitation_id,
                    workspace_id,
                    clean_email,
                    normalized_email,
                    role.value,
                    digest,
                    expires_at,
                    actor_user_id,
                    now,
                    surface="workspace invitation",
                )
                await self._event(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    event_type="workspace.invitation.issued",
                    subject_type="invitation",
                    subject_id=invitation_id,
                    details={"role": role.value},
                )
        except QueryError as exc:
            if "unique" in str(exc).casefold():
                raise DuplicateInvitationError(
                    "An active Workspace invitation already exists for that email address."
                ) from exc
            raise
        row = await self._invitation_by_id(workspace_id, invitation_id)
        assert row is not None
        return IssuedInvitation(self._invitation(row), token)

    async def _invitation_by_id(
        self, workspace_id: str, invitation_id: str
    ) -> _InvitationRow | None:
        return await self._fetch_one(
            _InvitationRow,
            "SELECT id, workspace_id, email, normalized_email, role, token_hash, expires_at, "
            "accepted_at, revoked_at, invited_by, created_at "
            "FROM workspace_core_invitations WHERE workspace_id = ? AND id = ?",
            workspace_id,
            invitation_id,
            surface="workspace invitation",
        )

    async def revoke_invitation(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        invitation_id: str,
    ) -> None:
        """Revoke an active invitation without accepting cross-tenant identifiers."""

        await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.MEMBERS_INVITE,),
        )
        now = self._stamp(self._now())
        async with self._db.transaction():
            await self._lock_workspace(workspace_id)
            row = await self._invitation_by_id(workspace_id, invitation_id)
            if row is None:
                raise NotFoundError("Workspace invitation was not found in this tenant.")
            if row.accepted_at is not None:
                raise InvitationConsumedError("Workspace invitation has already been accepted.")
            if row.revoked_at is not None:
                raise InvitationRevokedError("Workspace invitation has already been revoked.")
            await self._execute(
                "UPDATE workspace_core_invitations SET revoked_at = ? "
                "WHERE workspace_id = ? AND id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                now,
                workspace_id,
                invitation_id,
                surface="workspace invitation",
            )
            await self._event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                event_type="workspace.invitation.revoked",
                subject_type="invitation",
                subject_id=invitation_id,
            )

    async def _invitation_for_token(self, token: str) -> _InvitationRow | None:
        return await self._fetch_one(
            _InvitationRow,
            "SELECT id, workspace_id, email, normalized_email, role, token_hash, expires_at, "
            "accepted_at, revoked_at, invited_by, created_at "
            "FROM workspace_core_invitations WHERE token_hash = ?",
            token_digest(token),
            surface="invitation token",
        )

    def _validate_invitation_state(self, row: _InvitationRow) -> None:
        if row.revoked_at is not None:
            raise InvitationRevokedError("Workspace invitation has been revoked.")
        if row.accepted_at is not None:
            raise InvitationConsumedError("Workspace invitation has already been accepted.")
        if datetime.fromisoformat(row.expires_at) <= self._now():
            raise InvitationExpiredError("Workspace invitation has expired.")

    async def accept_invitation(self, *, token: str, user_id: str) -> InvitationAcceptance:
        """Accept an invitation for an existing matching local identity exactly once."""

        now = self._stamp(self._now())
        async with self._db.transaction():
            row = await self._invitation_for_token(token)
            if row is None:
                raise InvitationError("Workspace invitation token is invalid.")
            await self._lock_workspace(row.workspace_id)
            row = await self._invitation_for_token(token)
            assert row is not None
            self._validate_invitation_state(row)
            user_row = await self._user_row(user_id)
            if user_row is None or not user_row.is_active:
                raise InvitationError("Workspace invitation identity is missing or disabled.")
            if user_row.normalized_email != row.normalized_email:
                raise InvitationError("Workspace invitation does not match the signed-in identity.")
            if await self._membership_row(row.workspace_id, user_id) is not None:
                raise ConflictError("Workspace user is already a member of this tenant.")
            membership_id = self._id_factory()
            await self._execute(
                "INSERT INTO workspace_core_memberships "
                "(id, workspace_id, user_id, role, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                membership_id,
                row.workspace_id,
                user_id,
                row.role,
                now,
                now,
                surface="invited membership",
            )
            consumed = await self._execute(
                "UPDATE workspace_core_invitations SET accepted_at = ? "
                "WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                now,
                row.id,
                surface="invitation acceptance",
            )
            if consumed != 1:
                raise InvitationConsumedError("Workspace invitation was accepted concurrently.")
            await self._execute(
                "UPDATE workspace_core_users SET session_version = session_version + 1, "
                "updated_at = ? WHERE id = ?",
                now,
                user_id,
                surface="membership session invalidation",
            )
            await self._event(
                workspace_id=row.workspace_id,
                actor_user_id=user_id,
                event_type="workspace.invitation.accepted",
                subject_type="membership",
                subject_id=membership_id,
                details={"role": row.role},
            )
        accepted_user = await self._user_row(user_id)
        membership_row = await self._membership_row(row.workspace_id, user_id)
        assert accepted_user is not None and membership_row is not None
        return InvitationAcceptance(self._user(accepted_user), self._membership(membership_row))

    async def register_from_invitation(
        self,
        *,
        token: str,
        display_name: str,
        password: str,
    ) -> InvitationAcceptance:
        """Atomically create a matching local user and accept one invitation."""

        clean_name = self._validate_display_name(display_name)
        self._validate_password(password)
        password_hash = hash_password(password)
        now = self._stamp(self._now())
        user_id = self._id_factory()
        membership_id = self._id_factory()
        async with self._db.transaction():
            row = await self._invitation_for_token(token)
            if row is None:
                raise InvitationError("Workspace invitation token is invalid.")
            await self._lock_workspace(row.workspace_id)
            row = await self._invitation_for_token(token)
            assert row is not None
            self._validate_invitation_state(row)
            existing = await self._fetch_one(
                _UserRow,
                "SELECT id, email, normalized_email, password_hash, display_name, "
                "session_version, is_active, disabled_at, created_at, updated_at "
                "FROM workspace_core_users WHERE normalized_email = ?",
                row.normalized_email,
                surface="invited user",
            )
            if existing is not None:
                raise ConflictError(
                    "A local identity already exists; sign in before accepting this invitation."
                )
            await self._execute(
                "INSERT INTO workspace_core_users "
                "(id, email, normalized_email, password_hash, display_name, session_version, "
                "is_active, disabled_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 1, NULL, ?, ?)",
                user_id,
                row.email,
                row.normalized_email,
                password_hash,
                clean_name,
                now,
                now,
                surface="invited user",
            )
            await self._execute(
                "INSERT INTO workspace_core_memberships "
                "(id, workspace_id, user_id, role, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                membership_id,
                row.workspace_id,
                user_id,
                row.role,
                now,
                now,
                surface="invited membership",
            )
            consumed = await self._execute(
                "UPDATE workspace_core_invitations SET accepted_at = ? "
                "WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                now,
                row.id,
                surface="invitation acceptance",
            )
            if consumed != 1:
                raise InvitationConsumedError("Workspace invitation was accepted concurrently.")
            await self._event(
                workspace_id=row.workspace_id,
                actor_user_id=user_id,
                event_type="workspace.invitation.accepted",
                subject_type="membership",
                subject_id=membership_id,
                details={"role": row.role},
            )
        user_row = await self._user_row(user_id)
        membership_row = await self._membership_row(row.workspace_id, user_id)
        assert user_row is not None and membership_row is not None
        return InvitationAcceptance(self._user(user_row), self._membership(membership_row))

    async def change_role(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        target_user_id: str,
        role: Role,
    ) -> Membership:
        """Change a membership role while preserving one owner and admin bounds."""

        actor = await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.MEMBER_ROLES_CHANGE,),
        )
        now = self._stamp(self._now())
        async with self._db.transaction():
            await self._lock_workspace(workspace_id)
            target = await self._membership_row(workspace_id, target_user_id)
            if target is None:
                raise NotFoundError("Workspace membership was not found in this tenant.")
            target_role = Role(target.role)
            if actor.membership.role is not Role.OWNER and (
                target_role in {Role.OWNER, Role.ADMIN} or role in {Role.OWNER, Role.ADMIN}
            ):
                raise AuthorizationError(
                    "Workspace administrators may change only member and viewer roles."
                )
            if target_role is Role.OWNER and role is not Role.OWNER:
                owner_count = int(
                    await self._fetch_val(
                        "SELECT COUNT(*) FROM workspace_core_memberships "
                        "WHERE workspace_id = ? AND role = ?",
                        workspace_id,
                        Role.OWNER.value,
                        surface="workspace owners",
                    )
                    or 0
                )
                if owner_count <= 1:
                    raise LastOwnerError("Workspace must retain at least one owner.")
            await self._execute(
                "UPDATE workspace_core_memberships SET role = ?, updated_at = ? "
                "WHERE workspace_id = ? AND user_id = ?",
                role.value,
                now,
                workspace_id,
                target_user_id,
                surface="workspace membership role",
            )
            if role is not target_role:
                await self._execute(
                    "UPDATE workspace_core_users SET session_version = session_version + 1, "
                    "updated_at = ? WHERE id = ?",
                    now,
                    target_user_id,
                    surface="role session invalidation",
                )
            await self._event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                event_type="workspace.membership.role_changed",
                subject_type="membership",
                subject_id=target.id,
                details={"from": target.role, "to": role.value},
            )
        updated = await self._membership_row(workspace_id, target_user_id)
        assert updated is not None
        return self._membership(updated)

    async def remove_membership(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        target_user_id: str,
    ) -> None:
        """Remove one tenant membership without deleting the deployment-local user."""

        actor = await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.MEMBERS_MANAGE,),
        )
        now = self._stamp(self._now())
        async with self._db.transaction():
            await self._lock_workspace(workspace_id)
            target = await self._membership_row(workspace_id, target_user_id)
            if target is None:
                raise NotFoundError("Workspace membership was not found in this tenant.")
            target_role = Role(target.role)
            if actor.membership.role is not Role.OWNER and target_role in {
                Role.OWNER,
                Role.ADMIN,
            }:
                raise AuthorizationError(
                    "Workspace administrators cannot remove owners or administrators."
                )
            if target_role is Role.OWNER:
                owner_count = int(
                    await self._fetch_val(
                        "SELECT COUNT(*) FROM workspace_core_memberships "
                        "WHERE workspace_id = ? AND role = ?",
                        workspace_id,
                        Role.OWNER.value,
                        surface="workspace owners",
                    )
                    or 0
                )
                if owner_count <= 1:
                    raise LastOwnerError("Workspace must retain at least one owner.")
            await self._execute(
                "DELETE FROM workspace_core_memberships WHERE workspace_id = ? AND user_id = ?",
                workspace_id,
                target_user_id,
                surface="workspace membership",
            )
            await self._execute(
                "UPDATE workspace_core_users SET session_version = session_version + 1, "
                "updated_at = ? WHERE id = ?",
                now,
                target_user_id,
                surface="membership session invalidation",
            )
            await self._event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                event_type="workspace.membership.removed",
                subject_type="membership",
                subject_id=target.id,
            )

    async def issue_password_reset(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        target_user_id: str,
        lifetime: timedelta = timedelta(minutes=30),
    ) -> IssuedPasswordReset:
        """Issue one owner/admin-created local password-reset token."""

        actor = await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.MEMBERS_MANAGE,),
        )
        target = await self._membership_row(workspace_id, target_user_id)
        if target is None:
            raise NotFoundError("Workspace member was not found in this tenant.")
        if actor.membership.role is not Role.OWNER and Role(target.role) in {
            Role.OWNER,
            Role.ADMIN,
        }:
            raise AuthorizationError(
                "Workspace administrators cannot reset owner or administrator credentials."
            )
        if lifetime <= timedelta(0) or lifetime > timedelta(hours=24):
            raise ValidationError("Password reset lifetime must be between 1 second and 24 hours.")
        now_dt = self._now()
        now = self._stamp(now_dt)
        expires_at = self._stamp(now_dt + lifetime)
        token, digest = self._token_issuer()
        reset_id = self._id_factory()
        async with self._db.transaction():
            await self._lock_workspace(workspace_id)
            await self._execute(
                "UPDATE workspace_core_password_resets SET revoked_at = ? "
                "WHERE user_id = ? AND used_at IS NULL AND revoked_at IS NULL",
                now,
                target_user_id,
                surface="previous password reset",
            )
            await self._execute(
                "INSERT INTO workspace_core_password_resets "
                "(id, user_id, token_hash, expires_at, used_at, revoked_at, created_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                reset_id,
                target_user_id,
                digest,
                expires_at,
                now,
                surface="password reset",
            )
            await self._event(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                event_type="workspace.password_reset.issued",
                subject_type="user",
                subject_id=target_user_id,
            )
        row = await self._fetch_one(
            _PasswordResetRow,
            "SELECT id, user_id, token_hash, expires_at, used_at, revoked_at, created_at "
            "FROM workspace_core_password_resets WHERE id = ?",
            reset_id,
            surface="password reset",
        )
        assert row is not None
        return IssuedPasswordReset(self._reset(row), token)

    async def consume_password_reset(self, *, token: str, new_password: str) -> WorkspaceUser:
        """Replace a local password and invalidate all prior sessions exactly once."""

        self._validate_password(new_password)
        password_hash = hash_password(new_password)
        now = self._stamp(self._now())
        async with self._db.transaction():
            row = await self._fetch_one(
                _PasswordResetRow,
                "SELECT id, user_id, token_hash, expires_at, used_at, revoked_at, created_at "
                "FROM workspace_core_password_resets WHERE token_hash = ?",
                token_digest(token),
                surface="password reset token",
            )
            if row is None:
                raise PasswordResetError("Password reset token is invalid.")
            if row.revoked_at is not None:
                raise PasswordResetRevokedError("Password reset token has been revoked.")
            if row.used_at is not None:
                raise PasswordResetConsumedError("Password reset token has already been used.")
            if datetime.fromisoformat(row.expires_at) <= self._now():
                raise PasswordResetExpiredError("Password reset token has expired.")
            consumed = await self._execute(
                "UPDATE workspace_core_password_resets SET used_at = ? "
                "WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL",
                now,
                row.id,
                surface="password reset token",
            )
            if consumed != 1:
                raise PasswordResetConsumedError("Password reset token was used concurrently.")
            await self._execute(
                "UPDATE workspace_core_users SET password_hash = ?, "
                "session_version = session_version + 1, updated_at = ? WHERE id = ?",
                password_hash,
                now,
                row.user_id,
                surface="local password",
            )
            await self._event(
                workspace_id=None,
                actor_user_id=row.user_id,
                event_type="workspace.password_reset.consumed",
                subject_type="user",
                subject_id=row.user_id,
            )
        user_row = await self._user_row(row.user_id)
        if user_row is None:
            raise NotFoundError("Password reset user no longer exists.")
        return self._user(user_row)

    async def list_audit_events(
        self,
        *,
        actor_user_id: str,
        workspace_id: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Return recent tenant audit events only to owner/admin principals."""

        await self.require_permissions(
            user_id=actor_user_id,
            workspace_id=workspace_id,
            permissions=(Permission.AUDIT_READ,),
        )
        bounded_limit = min(max(limit, 1), 500)
        try:
            rows = await self._db.fetch(
                AuditEvent,
                "SELECT id, workspace_id, actor_user_id, event_type, subject_type, "
                "subject_id, details_json, created_at FROM workspace_core_audit_events "
                "WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                workspace_id,
                bounded_limit,
            )
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                "Workspace database is unavailable while reading audit events."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    "Workspace schema cannot read audit events; run the packaged Core migrations."
                ) from exc
            raise
        return rows
