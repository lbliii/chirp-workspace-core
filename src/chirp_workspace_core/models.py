"""Immutable records shared across Workspace Core repository boundaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType
from uuid import uuid4

UserId = NewType("UserId", str)
WorkspaceId = NewType("WorkspaceId", str)
MembershipId = NewType("MembershipId", str)
InvitationId = NewType("InvitationId", str)
PasswordResetId = NewType("PasswordResetId", str)
AuditEventId = NewType("AuditEventId", str)


def new_id() -> str:
    """Return a new opaque text UUID suitable for any Workspace record."""

    return str(uuid4())


class Role(StrEnum):
    """A membership's baseline authority within one workspace."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True)
class WorkspaceUser:
    """A secret-free local identity satisfying Chirp's user protocol.

    Password hashes are repository-private persistence details and must never
    enter request context, templates, logs, or the public model surface.
    Workspace permissions are deliberately empty here: authorization requires
    a separately resolved tenant membership on every request.
    """

    id: UserId
    email: str
    display_name: str
    session_version: int
    created_at: str
    updated_at: str
    is_active: bool = True
    disabled_at: str | None = None
    permissions: frozenset[str] = frozenset()

    @property
    def is_authenticated(self) -> bool:
        """Disabled identities fail Chirp's authentication protocol closed."""

        return self.is_active


@dataclass(frozen=True, slots=True)
class Workspace:
    """A tenant and authorization boundary."""

    id: WorkspaceId
    slug: str
    name: str
    created_at: str
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class Membership:
    """A user's current baseline role in one workspace."""

    id: MembershipId
    workspace_id: WorkspaceId
    user_id: UserId
    role: Role
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WorkspacePrincipal:
    """A request-scoped identity authorized for one explicit workspace."""

    user: WorkspaceUser
    workspace: Workspace
    membership: Membership
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Invitation:
    """A single-use, hashed invitation scoped to one workspace."""

    id: InvitationId
    workspace_id: WorkspaceId
    email: str
    role: Role
    token_hash: str
    expires_at: str
    accepted_at: str | None
    revoked_at: str | None
    invited_by: UserId
    created_at: str


@dataclass(frozen=True, slots=True)
class PasswordReset:
    """A single-use local-identity password reset; plaintext tokens are never stored."""

    id: PasswordResetId
    user_id: UserId
    token_hash: str
    expires_at: str
    used_at: str | None
    created_at: str
    revoked_at: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, secret-safe application audit record."""

    id: AuditEventId
    workspace_id: WorkspaceId | None
    actor_user_id: UserId | None
    event_type: str
    subject_type: str
    subject_id: str
    details_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class BootstrapState:
    """Durable proof that the one-time deployment setup token was consumed."""

    completed_at: str | None
    owner_user_id: UserId | None
    workspace_id: WorkspaceId | None
    id: int = 1


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Records created by the one-time first-owner bootstrap."""

    user: WorkspaceUser
    workspace: Workspace
    membership: Membership


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    """Invitation metadata plus the plaintext token returned exactly once."""

    invitation: Invitation
    token: str


@dataclass(frozen=True, slots=True)
class InvitationAcceptance:
    """Identity and membership resulting from invitation acceptance."""

    user: WorkspaceUser
    membership: Membership


@dataclass(frozen=True, slots=True)
class IssuedPasswordReset:
    """Password reset metadata plus the plaintext token returned exactly once."""

    reset: PasswordReset
    token: str
