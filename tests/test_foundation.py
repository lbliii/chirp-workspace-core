"""Acceptance coverage for the Workspace Core schema and role contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from chirp.data import Database, migrate

from chirp_workspace_core import (
    AuthorizationError,
    Permission,
    Role,
    WorkspaceUser,
    has_permission,
    permissions_for_role,
    require_permission,
)
from chirp_workspace_core.auth import chirp_auth_config
from chirp_workspace_core.migrations import migration_directory

pytestmark = pytest.mark.issue(770)


def test_installed_package_contains_ordered_workspace_migrations() -> None:
    directory = migration_directory()

    assert directory.is_dir()
    assert [path.name for path in sorted(directory.glob("*.sql"))] == [
        "001_workspace_core.sql",
        "002_workspace_activity.sql",
    ]
    assert (
        "CREATE TABLE workspace_core_memberships"
        in (directory / "001_workspace_core.sql").read_text()
    )


async def test_migrations_apply_once_and_replay_without_schema_drift(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'migrations.db'}")
    await database.connect()
    try:
        first = await migrate(database, migration_directory())
        replay = await migrate(database, migration_directory())

        assert first.applied == ["001_workspace_core", "002_workspace_activity"]
        assert replay.applied == []
        assert replay.already_applied == 2
        expected_tables = {
            "workspace_core_users",
            "workspace_core_workspaces",
            "workspace_core_memberships",
            "workspace_core_invitations",
            "workspace_core_password_resets",
            "workspace_core_audit_events",
            "workspace_core_bootstrap",
            "workspace_core_activity_events",
            "workspace_core_notification_sequence",
            "workspace_core_notifications",
        }
        for table in expected_tables:
            assert (
                await database.fetch_val(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                    table,
                )
                == 1
            )
    finally:
        await database.disconnect()


def test_role_matrix_is_monotonic_and_fails_closed() -> None:
    viewer = permissions_for_role(Role.VIEWER)
    member = permissions_for_role(Role.MEMBER)
    admin = permissions_for_role(Role.ADMIN)
    owner = permissions_for_role(Role.OWNER)

    assert viewer < member < admin < owner
    assert Permission.WORKSPACE_READ in viewer
    assert Permission.CONTENT_WRITE not in viewer
    assert Permission.MEMBERS_INVITE in admin
    assert Permission.WORKSPACE_DELETE not in admin
    assert Permission.WORKSPACE_DELETE in owner
    assert permissions_for_role("invented") == frozenset()
    assert has_permission(Role.OWNER, "invented") is False


def test_permission_guard_uses_exact_permissions() -> None:
    require_permission(Role.ADMIN, Permission.MEMBERS_INVITE)

    with pytest.raises(AuthorizationError, match="does not grant permission"):
        require_permission(Role.VIEWER, Permission.MEMBERS_INVITE)


def test_public_identity_is_secret_free_and_supports_session_revocation() -> None:
    user = WorkspaceUser(
        id="user-1",
        email="owner@example.test",
        display_name="Owner",
        session_version=3,
        created_at="2026-07-16T12:00:00+00:00",
        updated_at="2026-07-16T12:00:00+00:00",
    )

    assert user.is_authenticated is True
    assert user.permissions == frozenset()
    assert not hasattr(user, "password_hash")

    async def load_user(user_id: str) -> WorkspaceUser | None:
        return user if user_id == user.id else None

    config = chirp_auth_config(type("Loader", (), {"load_user": staticmethod(load_user)})())
    assert config.load_user is not None
    assert config.session_version is not None
    assert config.session_version(user) == 3
