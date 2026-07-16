"""Behavioral acceptance tests for the durable Workspace repository."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from chirp.data import Database, DatabaseConnectionError, migrate

from chirp_workspace_core import (
    AuthorizationError,
    ConflictError,
    DatabaseUnavailableError,
    DuplicateInvitationError,
    InvitationConsumedError,
    InvitationExpiredError,
    InvitationRevokedError,
    LastOwnerError,
    PasswordResetConsumedError,
    PasswordResetExpiredError,
    PasswordResetRevokedError,
    Permission,
    Role,
    SchemaMismatchError,
    ValidationError,
)
from chirp_workspace_core.migrations import migration_directory
from chirp_workspace_core.repository import WorkspaceRepository

pytestmark = pytest.mark.issue(770)

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a newer correct horse battery staple"


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
async def repository(tmp_path: Path) -> AsyncIterator[WorkspaceRepository]:
    database = Database(f"sqlite:///{tmp_path / 'workspace.db'}", pool_size=4)
    await database.connect()
    await migrate(database, migration_directory())
    yield WorkspaceRepository(database)
    await database.disconnect()


async def bootstrap(repository: WorkspaceRepository, *, slug: str = "primary"):
    return await repository.bootstrap_first_owner(
        expected_setup_token="deployment-setup-token",
        supplied_setup_token="deployment-setup-token",
        email="owner@example.test",
        display_name="Owner",
        password=PASSWORD,
        workspace_slug=slug,
        workspace_name="Primary Workspace",
    )


async def add_member(
    repository: WorkspaceRepository,
    *,
    owner_id: str,
    workspace_id: str,
    email: str = "member@example.test",
    role: Role = Role.MEMBER,
):
    user = await repository.create_user(email=email, display_name="Member", password=PASSWORD)
    issued = await repository.issue_invitation(
        actor_user_id=owner_id,
        workspace_id=workspace_id,
        email=email,
        role=role,
    )
    accepted = await repository.accept_invitation(token=issued.token, user_id=user.id)
    return user, accepted, issued


async def test_bootstrap_is_single_use_and_identity_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    database = Database(f"sqlite:///{path}")
    await database.connect()
    await migrate(database, migration_directory())
    repository = WorkspaceRepository(database)
    result = await bootstrap(repository)

    with pytest.raises(ConflictError, match="already complete"):
        await bootstrap(repository)

    assert await repository.authenticate(" OWNER@example.test ", PASSWORD) == result.user
    assert await repository.authenticate("owner@example.test", "wrong password") is None
    await database.disconnect()

    reopened = Database(f"sqlite:///{path}")
    await reopened.connect()
    try:
        replay = await migrate(reopened, migration_directory())
        restarted_repository = WorkspaceRepository(reopened)
        assert replay.applied == []
        assert await restarted_repository.load_user(result.user.id) == result.user
        principal = await restarted_repository.principal(
            user_id=result.user.id,
            workspace_id=result.workspace.id,
        )
        assert principal.membership.role is Role.OWNER
    finally:
        await reopened.disconnect()


async def test_cross_tenant_reads_writes_and_identifier_tampering_are_denied(
    repository: WorkspaceRepository,
) -> None:
    first = await bootstrap(repository)
    second_owner = await repository.create_user(
        email="second-owner@example.test",
        display_name="Second Owner",
        password=PASSWORD,
    )
    second_workspace, _ = await repository.create_workspace(
        owner_user_id=second_owner.id,
        slug="second",
        name="Second Workspace",
    )

    with pytest.raises(AuthorizationError, match="does not authorize"):
        await repository.principal(
            user_id=first.user.id,
            workspace_id=second_workspace.id,
        )
    with pytest.raises(AuthorizationError):
        await repository.issue_invitation(
            actor_user_id=first.user.id,
            workspace_id=second_workspace.id,
            email="victim@example.test",
            role=Role.MEMBER,
        )
    with pytest.raises(AuthorizationError):
        await repository.change_role(
            actor_user_id=first.user.id,
            workspace_id=second_workspace.id,
            target_user_id=second_owner.id,
            role=Role.VIEWER,
        )


async def test_role_changes_enforce_matrix_last_owner_and_session_invalidation(
    repository: WorkspaceRepository,
) -> None:
    result = await bootstrap(repository)
    stale_user, accepted, _ = await add_member(
        repository,
        owner_id=result.user.id,
        workspace_id=result.workspace.id,
    )
    assert accepted.user.session_version == stale_user.session_version + 1

    changed = await repository.change_role(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        target_user_id=stale_user.id,
        role=Role.ADMIN,
    )
    assert changed.role is Role.ADMIN
    reloaded = await repository.load_user(stale_user.id)
    assert reloaded is not None
    assert reloaded.session_version == accepted.user.session_version + 1
    await repository.require_permissions(
        user_id=stale_user.id,
        workspace_id=result.workspace.id,
        permissions=(Permission.MEMBERS_INVITE,),
    )

    with pytest.raises(AuthorizationError, match="Only a Workspace owner"):
        await repository.issue_invitation(
            actor_user_id=stale_user.id,
            workspace_id=result.workspace.id,
            email="admin-target@example.test",
            role=Role.ADMIN,
        )
    with pytest.raises(LastOwnerError):
        await repository.change_role(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            target_user_id=result.user.id,
            role=Role.ADMIN,
        )
    with pytest.raises(LastOwnerError):
        await repository.remove_membership(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            target_user_id=result.user.id,
        )


async def test_invitation_duplicate_expiry_revocation_and_replay(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 16, 12, tzinfo=UTC))
    database = Database(f"sqlite:///{tmp_path / 'invitations.db'}")
    await database.connect()
    await migrate(database, migration_directory())
    repository = WorkspaceRepository(database, clock=clock)
    try:
        result = await bootstrap(repository)
        issued = await repository.issue_invitation(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            email="member@example.test",
            role=Role.MEMBER,
            lifetime=timedelta(minutes=5),
        )
        with pytest.raises(DuplicateInvitationError):
            await repository.issue_invitation(
                actor_user_id=result.user.id,
                workspace_id=result.workspace.id,
                email=" MEMBER@example.test ",
                role=Role.VIEWER,
            )

        await repository.revoke_invitation(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            invitation_id=issued.invitation.id,
        )
        user = await repository.create_user(
            email="member@example.test", display_name="Member", password=PASSWORD
        )
        with pytest.raises(InvitationRevokedError):
            await repository.accept_invitation(token=issued.token, user_id=user.id)

        expiring = await repository.issue_invitation(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            email="member@example.test",
            role=Role.MEMBER,
            lifetime=timedelta(seconds=1),
        )
        clock.advance(timedelta(seconds=2))
        with pytest.raises(InvitationExpiredError):
            await repository.accept_invitation(token=expiring.token, user_id=user.id)

        replacement = await repository.issue_invitation(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            email="member@example.test",
            role=Role.MEMBER,
        )
        await repository.accept_invitation(token=replacement.token, user_id=user.id)
        with pytest.raises(InvitationConsumedError):
            await repository.accept_invitation(token=replacement.token, user_id=user.id)
    finally:
        await database.disconnect()


async def test_password_reset_rotates_credentials_sessions_and_tokens(
    repository: WorkspaceRepository,
) -> None:
    result = await bootstrap(repository)
    member, accepted, _ = await add_member(
        repository,
        owner_id=result.user.id,
        workspace_id=result.workspace.id,
    )
    first = await repository.issue_password_reset(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        target_user_id=member.id,
    )
    replacement = await repository.issue_password_reset(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        target_user_id=member.id,
    )
    with pytest.raises(PasswordResetRevokedError):
        await repository.consume_password_reset(token=first.token, new_password=NEW_PASSWORD)

    reset_user = await repository.consume_password_reset(
        token=replacement.token,
        new_password=NEW_PASSWORD,
    )
    assert reset_user.session_version == accepted.user.session_version + 1
    assert await repository.authenticate(member.email, PASSWORD) is None
    assert await repository.authenticate(member.email, NEW_PASSWORD) == reset_user
    with pytest.raises(PasswordResetConsumedError):
        await repository.consume_password_reset(token=replacement.token, new_password=PASSWORD)


async def test_expired_password_reset_is_rejected(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 7, 16, 12, tzinfo=UTC))
    database = Database(f"sqlite:///{tmp_path / 'reset-expiry.db'}")
    await database.connect()
    await migrate(database, migration_directory())
    repository = WorkspaceRepository(database, clock=clock)
    try:
        result = await bootstrap(repository)
        member, _, _ = await add_member(
            repository,
            owner_id=result.user.id,
            workspace_id=result.workspace.id,
        )
        reset = await repository.issue_password_reset(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            target_user_id=member.id,
            lifetime=timedelta(seconds=1),
        )
        clock.advance(timedelta(seconds=2))
        with pytest.raises(PasswordResetExpiredError):
            await repository.consume_password_reset(token=reset.token, new_password=NEW_PASSWORD)
    finally:
        await database.disconnect()


async def test_concurrent_duplicate_invitation_has_one_winner(
    repository: WorkspaceRepository,
) -> None:
    result = await bootstrap(repository)

    outcomes = await asyncio.gather(
        *(
            repository.issue_invitation(
                actor_user_id=result.user.id,
                workspace_id=result.workspace.id,
                email="racer@example.test",
                role=Role.MEMBER,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    assert sum(isinstance(value, DuplicateInvitationError) for value in outcomes) == 1


async def test_concurrent_owner_mutations_cannot_remove_every_owner(
    repository: WorkspaceRepository,
) -> None:
    result = await bootstrap(repository)
    second_owner, _, _ = await add_member(
        repository,
        owner_id=result.user.id,
        workspace_id=result.workspace.id,
    )
    await repository.change_role(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        target_user_id=second_owner.id,
        role=Role.OWNER,
    )

    outcomes = await asyncio.gather(
        repository.change_role(
            actor_user_id=result.user.id,
            workspace_id=result.workspace.id,
            target_user_id=second_owner.id,
            role=Role.ADMIN,
        ),
        repository.change_role(
            actor_user_id=second_owner.id,
            workspace_id=result.workspace.id,
            target_user_id=result.user.id,
            role=Role.ADMIN,
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    assert sum(isinstance(value, LastOwnerError) for value in outcomes) == 1
    first = await repository.principal(
        user_id=result.user.id,
        workspace_id=result.workspace.id,
    )
    second = await repository.principal(
        user_id=second_owner.id,
        workspace_id=result.workspace.id,
    )
    assert {first.membership.role, second.membership.role} == {Role.OWNER, Role.ADMIN}


async def test_malformed_unique_and_schema_mismatch_paths_are_domain_errors(
    tmp_path: Path,
    repository: WorkspaceRepository,
) -> None:
    with pytest.raises(ValidationError, match="email"):
        await repository.create_user(email="malformed", display_name="Bad", password=PASSWORD)
    await repository.create_user(
        email="unique@example.test", display_name="Unique", password=PASSWORD
    )
    with pytest.raises(ConflictError, match="already exists"):
        await repository.create_user(
            email=" UNIQUE@example.test ", display_name="Duplicate", password=PASSWORD
        )

    unmigrated = Database(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    await unmigrated.connect()
    try:
        with pytest.raises(SchemaMismatchError, match="schema"):
            await WorkspaceRepository(unmigrated).load_user("tampered-id")
    finally:
        await unmigrated.disconnect()


async def test_unavailable_database_is_reported_at_repository_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database("sqlite:///:memory:")

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise DatabaseConnectionError("database is offline")

    monkeypatch.setattr(Database, "fetch_one", unavailable)

    with pytest.raises(DatabaseUnavailableError, match="unavailable"):
        await WorkspaceRepository(database).load_user("user-id")
