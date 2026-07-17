"""Local acceptance coverage for the Workspace Core conformance consumer."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from chirp.config import AppConfig
from chirp.data import Database, DatabaseConnectionError, MigrationError, migrate
from chirp.testing import TestClient

from chirp_workspace_core import Permission, Role
from chirp_workspace_core.migrations import migration_directory
from chirp_workspace_core.repository import WorkspaceRepository
from conformance import migrate as conformance_migrate
from conformance.application import (
    STATIC,
    TEMPLATES,
    ConformanceRuntime,
    ConformanceSettings,
    create_conformance_runtime,
)

pytestmark = pytest.mark.issue(772)

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery staple"
SETUP_TOKEN = "local-conformance-setup-token"
SECRET_KEY = "local-conformance-secret-key-with-32-bytes"


def extract_csrf(html: str) -> str:
    patterns = (
        r'name="_csrf_token" value="([^"]+)"',
        r'value="([^"]+)"[^>]*name="_csrf_token"',
    )
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    raise AssertionError("rendered form did not include a CSRF token")


def response_cookie(response: object) -> str | None:
    for name, value in getattr(response, "headers", ()):
        if name.lower() == "set-cookie" and value.startswith("chirp_session="):
            return value.split(";", 1)[0].partition("=")[2]
    return None


def cookie_headers(cookie: str | None, **headers: str) -> dict[str, str]:
    if cookie is not None:
        headers["Cookie"] = f"chirp_session={cookie}"
    return headers


async def login_user(
    client: TestClient,
    *,
    email: str,
    password: str,
    cookie: str | None = None,
) -> tuple[object, str | None]:
    page = await client.get("/login", headers=cookie_headers(cookie))
    token = extract_csrf(page.text)
    current_cookie = response_cookie(page) or cookie
    response = await client.post(
        "/login",
        data={"email": email, "password": password, "_csrf_token": token},
        headers=cookie_headers(current_cookie),
    )
    return response, response_cookie(response) or current_cookie


async def post_form(
    client: TestClient,
    path: str,
    *,
    via: str,
    cookie: str | None,
    data: dict[str, str],
    htmx: bool,
) -> tuple[object, str | None]:
    page = await client.get(via, headers=cookie_headers(cookie))
    token = extract_csrf(page.text)
    current_cookie = response_cookie(page) or cookie
    headers = cookie_headers(current_cookie)
    if htmx:
        headers["HX-Request"] = "true"
    response = await client.post(
        path,
        data={**data, "_csrf_token": token},
        headers=headers,
    )
    return response, response_cookie(response) or current_cookie


@dataclass(frozen=True, slots=True)
class RunningConformance:
    runtime: ConformanceRuntime
    client: TestClient


def build_runtime(database_url: str, *, secret_key: str = SECRET_KEY) -> ConformanceRuntime:
    settings = ConformanceSettings(
        database_url=database_url,
        setup_token=SETUP_TOKEN,
        secret_key=secret_key,
    )
    config = AppConfig(
        template_dir=TEMPLATES,
        component_dirs=(migration_directory().parent / "templates",),
        static_dir=STATIC,
        secret_key=secret_key,
        csp_nonce_enabled=True,
        strict_transport_security="max-age=86400",
        workers=1,
        worker_mode="async",
    )
    return create_conformance_runtime(settings, config=config)


@pytest.fixture
async def conformance(tmp_path: Path) -> AsyncIterator[RunningConformance]:
    runtime = build_runtime(f"sqlite:///{tmp_path / 'conformance.db'}")
    async with TestClient(runtime.app) as client:
        yield RunningConformance(runtime=runtime, client=client)


async def test_bootstrap_state_and_principals_follow_durable_memberships(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'principals.db'}")
    await database.connect()
    await migrate(database, migration_directory())
    repository = WorkspaceRepository(database)
    try:
        initial = await repository.bootstrap_state()
        assert initial.id == 1
        assert initial.completed_at is None
        assert initial.owner_user_id is None
        assert initial.workspace_id is None

        result = await repository.bootstrap_first_owner(
            expected_setup_token=SETUP_TOKEN,
            supplied_setup_token=SETUP_TOKEN,
            email="owner@example.test",
            display_name="Owner",
            password=PASSWORD,
            workspace_slug="primary",
            workspace_name="Zulu Workspace",
        )
        second_workspace, _ = await repository.create_workspace(
            owner_user_id=result.user.id,
            slug="alpha",
            name="Alpha Workspace",
        )

        completed = await repository.bootstrap_state()
        assert completed.completed_at is not None
        assert completed.owner_user_id == result.user.id
        assert completed.workspace_id == result.workspace.id

        principals = await repository.principals_for_user(result.user.id)
        assert [principal.workspace.name for principal in principals] == [
            "Alpha Workspace",
            "Zulu Workspace",
        ]
        assert {principal.workspace.id for principal in principals} == {
            result.workspace.id,
            second_workspace.id,
        }
        assert all(principal.membership.role is Role.OWNER for principal in principals)
        assert all(Permission.WORKSPACE_DELETE in principal.permissions for principal in principals)

        unscoped = await repository.create_user(
            email="unscoped@example.test",
            display_name="Unscoped",
            password=PASSWORD,
        )
        assert await repository.principals_for_user(unscoped.id) == ()
        assert await repository.principals_for_user("tampered-user-id") == ()
    finally:
        await database.disconnect()


async def test_conformance_setup_page_claims_once_and_readiness_is_healthy(
    conformance: RunningConformance,
) -> None:
    client = conformance.client
    ready = await client.get("/ready")
    health = await client.get("/health")
    favicon = await client.get("/static/favicon.svg")
    htmx = await client.get("/static/vendor/htmx-2.0.10.min.js")
    htmx_sse = await client.get("/static/vendor/htmx-ext-sse-2.2.4.min.js")
    core_css = await client.get("/_workspace-core/shell.css")
    core_javascript = await client.get("/_workspace-core/shell.js")
    page = await client.get("/")

    assert ready.status == 200
    assert health.status == 200
    assert favicon.status == 200
    assert "image/svg+xml" in favicon.content_type
    assert "<svg" in favicon.text
    assert htmx.status == 200
    assert "text/javascript" in htmx.content_type
    assert htmx_sse.status == 200
    assert "text/javascript" in htmx_sse.content_type
    assert core_css.status == 200
    assert "text/css" in core_css.content_type
    assert ".workspace-main" in core_css.text
    assert core_javascript.status == 200
    assert "text/javascript" in core_javascript.content_type
    assert "data-workspace-command-open" in core_javascript.text
    assert page.status == 200
    assert "Claim your first workspace" in page.text
    assert 'name="_csrf_token"' in page.text
    token = extract_csrf(page.text)
    cookie = response_cookie(page)
    claimed = await client.post(
        "/setup",
        data={
            "setup_token": SETUP_TOKEN,
            "email": "owner@example.test",
            "display_name": "Owner",
            "password": PASSWORD,
            "workspace_slug": "primary",
            "workspace_name": "Primary Workspace",
            "_csrf_token": token,
        },
        headers=cookie_headers(cookie),
    )
    session_cookie = response_cookie(claimed) or cookie

    assert claimed.status == 303
    assert claimed.header("location").startswith("/workspaces/")
    workspace = await client.get(
        claimed.header("location"),
        headers=cookie_headers(session_cookie),
    )
    assert workspace.status == 200
    policies = [
        value for name, value in workspace.headers if name.lower() == "content-security-policy"
    ]
    transport = [
        value for name, value in workspace.headers if name.lower() == "strict-transport-security"
    ]
    assert len(policies) == 1
    assert "'nonce-" in policies[0]
    assert transport == ["max-age=86400"]
    assert "<!doctype html>" in workspace.text
    assert "Foundation checks" in workspace.text
    assert "Primary Workspace" in workspace.text
    assert 'id="workspace-sidebar"' in workspace.text
    assert 'hx-target="#workspace-main"' in workspace.text
    assert 'hx-select="#workspace-main"' in workspace.text
    assert 'hx-disinherit="hx-select hx-target"' in workspace.text
    assert workspace.text.index("htmx-2.0.10.min.js") < workspace.text.index(
        "htmx-ext-sse-2.2.4.min.js"
    )

    narrow = await client.get(
        claimed.header("location"),
        headers=cookie_headers(session_cookie, **{"HX-Request": "true"}),
    )
    boosted = await client.get(
        claimed.header("location"),
        headers=cookie_headers(
            session_cookie,
            **{"HX-Request": "true", "HX-Boosted": "true"},
        ),
    )
    assert narrow.status == 200
    assert "<!doctype html>" not in narrow.text
    assert 'id="workspace-main"' not in narrow.text
    assert "Foundation checks" not in narrow.text
    assert "Save a persistence test" in narrow.text
    assert boosted.status == 200
    assert "<!doctype html>" not in boosted.text
    assert 'id="workspace-main"' in boosted.text
    assert "Foundation checks" in boosted.text

    replay_token = extract_csrf(workspace.text)
    replay_cookie = response_cookie(workspace) or session_cookie
    replay = await client.post(
        "/setup",
        data={
            "setup_token": SETUP_TOKEN,
            "email": "owner@example.test",
            "display_name": "Owner",
            "password": PASSWORD,
            "workspace_slug": "primary",
            "workspace_name": "Primary Workspace",
            "_csrf_token": replay_token,
        },
        headers=cookie_headers(replay_cookie),
    )
    assert replay.status == 422
    assert "already complete" in replay.text

    replay_page = await client.get("/", headers=cookie_headers(None))
    assert "Sign in" in replay_page.text

    await conformance.runtime.database.execute("DROP TABLE workspace_core_bootstrap")
    unhealthy = await client.get("/ready")
    assert unhealthy.status == 503
    assert "Workspace Core schema is unavailable or incompatible" in unhealthy.text


async def test_conformance_session_tenant_isolation_and_version_invalidation(
    conformance: RunningConformance,
) -> None:
    runtime = conformance.runtime
    client = conformance.client
    result = await runtime.identities.bootstrap_first_owner(
        expected_setup_token=SETUP_TOKEN,
        supplied_setup_token=SETUP_TOKEN,
        email="owner@example.test",
        display_name="Owner",
        password=PASSWORD,
        workspace_slug="primary",
        workspace_name="Primary Workspace",
    )
    other_owner = await runtime.identities.create_user(
        email="other@example.test",
        display_name="Other Owner",
        password=PASSWORD,
    )
    other_workspace, _ = await runtime.identities.create_workspace(
        owner_user_id=other_owner.id,
        slug="other",
        name="Other Workspace",
    )

    invalid, _ = await login_user(
        client,
        email=result.user.email,
        password="incorrect password",
    )
    assert invalid.status == 401
    assert "Invalid email or password" in invalid.text

    logged_in, cookie = await login_user(
        client,
        email=result.user.email,
        password=PASSWORD,
    )
    assert logged_in.status == 303
    own_path = f"/workspaces/{result.workspace.id}"
    own = await client.get(own_path, headers=cookie_headers(cookie))
    foreign = await client.get(
        f"/workspaces/{other_workspace.id}",
        headers=cookie_headers(cookie),
    )
    anonymous_stream = await client.get(
        f"{own_path}/notifications/events",
        headers=cookie_headers(None),
    )
    assert own.status == 200
    assert 'method="post" action="/logout"' in own.text
    assert 'name="_csrf_token"' in own.text
    assert foreign.status == 404
    assert anonymous_stream.status == 404

    foreign_stream = await client.get(
        f"/workspaces/{other_workspace.id}/notifications/events",
        headers=cookie_headers(cookie),
    )
    assert foreign_stream.status == 404

    foreign_write, cookie = await post_form(
        client,
        f"/workspaces/{other_workspace.id}/activity",
        via=own_path,
        cookie=cookie,
        data={"note": "cross-tenant write"},
        htmx=True,
    )
    assert foreign_write.status == 404
    assert await runtime.activity.list_activity(workspace_id=result.workspace.id) == ()
    assert await runtime.activity.list_activity(workspace_id=other_workspace.id) == ()
    assert (
        await runtime.activity.notifications_after(
            workspace_id=result.workspace.id,
            user_id=result.user.id,
        )
        == ()
    )
    assert (
        await runtime.activity.notifications_after(
            workspace_id=other_workspace.id,
            user_id=other_owner.id,
        )
        == ()
    )

    viewer = await runtime.identities.create_user(
        email="viewer@example.test",
        display_name="Viewer",
        password=PASSWORD,
    )
    invitation = await runtime.identities.issue_invitation(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        email=viewer.email,
        role=Role.VIEWER,
    )
    await runtime.identities.accept_invitation(token=invitation.token, user_id=viewer.id)
    viewer_login, viewer_cookie = await login_user(
        client,
        email=viewer.email,
        password=PASSWORD,
    )
    assert viewer_login.status == 303
    denied_write, _ = await post_form(
        client,
        f"{own_path}/activity",
        via=own_path,
        cookie=viewer_cookie,
        data={"note": "viewer write"},
        htmx=True,
    )
    assert denied_write.status == 403
    assert await runtime.activity.list_activity(workspace_id=result.workspace.id) == ()
    assert (
        await runtime.activity.notifications_after(
            workspace_id=result.workspace.id,
            user_id=viewer.id,
        )
        == ()
    )

    reset = await runtime.identities.issue_password_reset(
        actor_user_id=result.user.id,
        workspace_id=result.workspace.id,
        target_user_id=result.user.id,
    )
    await runtime.identities.consume_password_reset(
        token=reset.token,
        new_password=NEW_PASSWORD,
    )
    stale = await client.get(own_path, headers=cookie_headers(cookie))
    assert stale.status == 404

    relogged, new_cookie = await login_user(
        client,
        email=result.user.email,
        password=NEW_PASSWORD,
        cookie=cookie,
    )
    assert relogged.status == 303
    logout, logged_out_cookie = await post_form(
        client,
        "/logout",
        via=own_path,
        cookie=new_cookie,
        data={},
        htmx=False,
    )
    assert logout.status == 303
    after_logout = await client.get("/", headers=cookie_headers(logged_out_cookie))
    assert "Sign in" in after_logout.text


async def test_conformance_activity_supports_htmx_and_plain_form_fallback(
    conformance: RunningConformance,
) -> None:
    runtime = conformance.runtime
    client = conformance.client
    result = await runtime.identities.bootstrap_first_owner(
        expected_setup_token=SETUP_TOKEN,
        supplied_setup_token=SETUP_TOKEN,
        email="owner@example.test",
        display_name="Owner",
        password=PASSWORD,
        workspace_slug="primary",
        workspace_name="Primary Workspace",
    )
    logged_in, cookie = await login_user(
        client,
        email=result.user.email,
        password=PASSWORD,
    )
    assert logged_in.status == 303
    workspace_path = f"/workspaces/{result.workspace.id}"
    activity_path = f"{workspace_path}/activity"

    htmx, cookie = await post_form(
        client,
        activity_path,
        via=workspace_path,
        cookie=cookie,
        data={"note": "HTMX persistence proof"},
        htmx=True,
    )
    assert htmx.status == 200
    assert "HTMX persistence proof" in htmx.text
    assert "workspace-activity" in htmx.text
    assert 'id="workspace-notification-list"' in htmx.text
    assert 'hx-swap-oob="afterbegin"' in htmx.text

    persisted = await client.get(workspace_path, headers=cookie_headers(cookie))
    assert persisted.status == 200
    assert "HTMX persistence proof" in persisted.text
    assert 'id="workspace-sidebar"' in persisted.text
    assert 'id="activity-feed"' in persisted.text
    assert 'hx-disinherit="hx-select hx-target"' in persisted.text
    assert re.search(
        rf'<a\s+href="{re.escape(workspace_path)}"\s+'
        r'hx-target="#workspace-main"\s+hx-select="#workspace-main"',
        persisted.text,
    )

    plain, _ = await post_form(
        client,
        activity_path,
        via=workspace_path,
        cookie=cookie,
        data={"note": "Plain form persistence proof"},
        htmx=False,
    )
    assert plain.status == 303
    assert plain.header("location") == workspace_path
    activities = await runtime.activity.list_activity(workspace_id=result.workspace.id)
    assert {event.metadata["note"] for event in activities} == {
        "HTMX persistence proof",
        "Plain form persistence proof",
    }


async def test_conformance_restart_preserves_identity_activity_and_same_secret_session(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    first_runtime = build_runtime(database_url)
    async with TestClient(first_runtime.app) as first_client:
        result = await first_runtime.identities.bootstrap_first_owner(
            expected_setup_token=SETUP_TOKEN,
            supplied_setup_token=SETUP_TOKEN,
            email="owner@example.test",
            display_name="Owner",
            password=PASSWORD,
            workspace_slug="primary",
            workspace_name="Primary Workspace",
        )
        logged_in, cookie = await login_user(
            first_client,
            email=result.user.email,
            password=PASSWORD,
        )
        assert logged_in.status == 303
        workspace_path = f"/workspaces/{result.workspace.id}"
        recorded, cookie = await post_form(
            first_client,
            f"{workspace_path}/activity",
            via=workspace_path,
            cookie=cookie,
            data={"note": "Survives application restart"},
            htmx=True,
        )
        assert recorded.status == 200
        assert cookie is not None

    restarted_runtime = build_runtime(database_url)
    async with TestClient(restarted_runtime.app) as restarted_client:
        ready = await restarted_client.get("/ready")
        page = await restarted_client.get(
            workspace_path,
            headers=cookie_headers(cookie),
        )
        state = await restarted_runtime.identities.bootstrap_state()
        principals = await restarted_runtime.identities.principals_for_user(result.user.id)

        assert ready.status == 200
        assert page.status == 200
        assert "Survives application restart" in page.text
        assert state.workspace_id == result.workspace.id
        assert [principal.workspace.id for principal in principals] == [result.workspace.id]


async def test_conformance_secret_rotation_rejects_old_cookie_and_allows_fresh_login(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'secret-rotation.db'}"
    first_runtime = build_runtime(database_url)
    async with TestClient(first_runtime.app) as first_client:
        result = await first_runtime.identities.bootstrap_first_owner(
            expected_setup_token=SETUP_TOKEN,
            supplied_setup_token=SETUP_TOKEN,
            email="owner@example.test",
            display_name="Owner",
            password=PASSWORD,
            workspace_slug="primary",
            workspace_name="Primary Workspace",
        )
        logged_in, old_cookie = await login_user(
            first_client,
            email=result.user.email,
            password=PASSWORD,
        )
        assert logged_in.status == 303
        assert old_cookie is not None
        protected = await first_client.get(
            f"/workspaces/{result.workspace.id}",
            headers=cookie_headers(old_cookie),
        )
        assert protected.status == 200

    rotated_secret = "rotated-conformance-secret-key-with-32-bytes"
    rotated_runtime = build_runtime(database_url, secret_key=rotated_secret)
    workspace_path = f"/workspaces/{result.workspace.id}"
    async with TestClient(rotated_runtime.app) as rotated_client:
        rejected = await rotated_client.get(
            workspace_path,
            headers=cookie_headers(old_cookie),
        )
        public = await rotated_client.get("/", headers=cookie_headers(old_cookie))
        fresh_login, fresh_cookie = await login_user(
            rotated_client,
            email=result.user.email,
            password=PASSWORD,
            cookie=old_cookie,
        )

        assert rejected.status == 404
        assert "Sign in" in public.text
        assert fresh_login.status == 303
        assert fresh_cookie is not None
        assert fresh_cookie != old_cookie
        accepted = await rotated_client.get(
            workspace_path,
            headers=cookie_headers(fresh_cookie),
        )
        assert accepted.status == 200


async def test_conformance_readiness_fails_closed_while_database_is_unavailable(
    conformance: RunningConformance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: object, **kwargs: object) -> None:
        raise DatabaseConnectionError("database is offline")

    monkeypatch.setattr(Database, "fetch_one", unavailable)
    health = await conformance.client.get("/health")
    ready = await conformance.client.get("/ready")

    assert health.status == 200
    assert ready.status == 503
    assert "Workspace Core schema is unavailable or incompatible" in ready.text


async def test_predeploy_migration_failure_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_invalid.sql").write_text("THIS IS NOT SQL;", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migration-failure.db'}")
    monkeypatch.setattr(conformance_migrate, "migration_directory", lambda: migrations)

    with pytest.raises(MigrationError, match=r"Migration .* failed"):
        await conformance_migrate.main()
