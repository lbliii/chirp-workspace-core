"""Acceptance coverage for the durable activity stream and Workspace shell."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest
from chirp import App
from chirp.config import AppConfig
from chirp.data import Database, migrate
from chirp.templating.returns import Page, Template
from chirp.testing import TestClient

from chirp_workspace_core import (
    AuthorizationError,
    ConflictError,
    Membership,
    NotFoundError,
    Permission,
    Role,
    UserId,
    ValidationError,
    Workspace,
    WorkspaceId,
    WorkspacePrincipal,
    WorkspaceUser,
    permissions_for_role,
)
from chirp_workspace_core.activity import (
    ActivityRepository,
    notification_event_stream,
    parse_last_event_id,
)
from chirp_workspace_core.events import (
    ActivityEvent,
    ActivityId,
    Notification,
    NotificationDraft,
    NotificationId,
    ResourceId,
    ResourceReference,
)
from chirp_workspace_core.migrations import migration_directory
from chirp_workspace_core.repository import WorkspaceRepository
from chirp_workspace_core.shell import (
    Breadcrumb,
    NavigationItem,
    ShellCommand,
    WorkspaceChoice,
    build_shell_context,
    notification_fragment,
    notification_oob,
    register_shell_assets,
    workspace_templates_dir,
)

pytestmark = pytest.mark.issue(765)

PASSWORD = "correct horse battery staple"
STAMP = "2026-07-16T12:00:00+00:00"


@dataclass(frozen=True, slots=True)
class ActivityFixture:
    database: Database
    repository: ActivityRepository
    identities: WorkspaceRepository
    owner: WorkspaceUser
    member: WorkspaceUser
    workspace: Workspace
    other_owner: WorkspaceUser
    other_workspace: Workspace


@pytest.fixture
async def activity_fixture(tmp_path: Path) -> AsyncIterator[ActivityFixture]:
    database = Database(f"sqlite:///{tmp_path / 'activity.db'}", pool_size=4)
    await database.connect()
    await migrate(database, migration_directory())
    identities = WorkspaceRepository(database)
    bootstrapped = await identities.bootstrap_first_owner(
        expected_setup_token="setup-token",
        supplied_setup_token="setup-token",
        email="owner@example.test",
        display_name="Owner",
        password=PASSWORD,
        workspace_slug="primary",
        workspace_name="Primary Workspace",
    )
    member = await identities.create_user(
        email="member@example.test",
        display_name="Member",
        password=PASSWORD,
    )
    invitation = await identities.issue_invitation(
        actor_user_id=bootstrapped.user.id,
        workspace_id=bootstrapped.workspace.id,
        email=member.email,
        role=Role.MEMBER,
    )
    await identities.accept_invitation(token=invitation.token, user_id=member.id)
    other_owner = await identities.create_user(
        email="other-owner@example.test",
        display_name="Other Owner",
        password=PASSWORD,
    )
    other_workspace, _ = await identities.create_workspace(
        owner_user_id=other_owner.id,
        slug="other",
        name="Other Workspace",
    )
    yield ActivityFixture(
        database=database,
        repository=ActivityRepository(database),
        identities=identities,
        owner=bootstrapped.user,
        member=member,
        workspace=bootstrapped.workspace,
        other_owner=other_owner,
        other_workspace=other_workspace,
    )
    await database.disconnect()


def resource(workspace_id: WorkspaceId, *, suffix: str = "one") -> ResourceReference:
    return ResourceReference(
        workspace_id=workspace_id,
        product="board",
        kind="card",
        id=ResourceId(f"card-{suffix}"),
        url=f"/boards/cards/{suffix}",
    )


def activity(
    workspace_id: WorkspaceId,
    actor_user_id: UserId,
    *,
    suffix: str = "one",
) -> ActivityEvent:
    return ActivityEvent(
        id=ActivityId(f"activity-{suffix}"),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="board.card.updated",
        resource=resource(workspace_id, suffix=suffix),
        occurred_at=STAMP,
        metadata={"field": "title", "revision": 2},
    )


def draft(user_id: UserId, *, suffix: str = "one") -> NotificationDraft:
    return NotificationDraft(
        user_id=user_id,
        title=f"Card {suffix} changed",
        body="The card title was updated.",
        url=f"/boards/cards/{suffix}",
    )


def shell_principal(*, role: Role = Role.ADMIN) -> WorkspacePrincipal:
    user = WorkspaceUser(
        id=UserId("user-shell"),
        email="shell@example.test",
        display_name="Shell User",
        session_version=1,
        created_at=STAMP,
        updated_at=STAMP,
    )
    workspace = Workspace(
        id=WorkspaceId("workspace-shell"),
        slug="shell",
        name="Shell Workspace",
        created_at=STAMP,
        updated_at=STAMP,
    )
    membership = Membership(
        id="membership-shell",
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        created_at=STAMP,
        updated_at=STAMP,
    )
    return WorkspacePrincipal(
        user=user,
        workspace=workspace,
        membership=membership,
        permissions=frozenset(str(permission) for permission in permissions_for_role(role)),
    )


def shell_notification(principal: WorkspacePrincipal) -> Notification:
    return Notification(
        id=NotificationId("notification-shell"),
        workspace_id=principal.workspace.id,
        user_id=principal.user.id,
        activity_id=ActivityId("activity-shell"),
        sequence=1,
        title="Card changed",
        body="A card needs your attention.",
        url="/boards/cards/one",
        created_at=STAMP,
    )


def shell_context(principal: WorkspacePrincipal):
    own_notification = shell_notification(principal)
    foreign_recipient = Notification(
        id=NotificationId("notification-foreign-recipient"),
        workspace_id=principal.workspace.id,
        user_id=UserId("different-user"),
        activity_id=ActivityId("activity-foreign-recipient"),
        sequence=2,
        title="Private recipient notification",
        body="Must not enter this shell.",
        url="/private-recipient",
        created_at=STAMP,
    )
    foreign_workspace = Notification(
        id=NotificationId("notification-foreign-workspace"),
        workspace_id=WorkspaceId("different-workspace"),
        user_id=principal.user.id,
        activity_id=ActivityId("activity-foreign-workspace"),
        sequence=3,
        title="Private workspace notification",
        body="Must not enter this shell.",
        url="/private-workspace",
        created_at=STAMP,
    )
    return build_shell_context(
        principal,
        product_name="Board",
        primary_navigation=(
            NavigationItem(
                label="Board",
                url="/boards",
                product="board",
                required_permissions=frozenset({Permission.CONTENT_READ}),
                active=True,
            ),
            NavigationItem(
                label="Workspace settings",
                url="/settings",
                product="workspace",
                required_permissions=frozenset({Permission.WORKSPACE_DELETE}),
            ),
        ),
        product_navigation=(NavigationItem(label="My cards", url="/boards/mine", product="board"),),
        workspace_choices=(
            WorkspaceChoice(
                id=principal.workspace.id,
                name=principal.workspace.name,
                url="/workspaces/shell",
                current=True,
            ),
        ),
        breadcrumbs=(Breadcrumb("Board", "/boards"), Breadcrumb("My cards")),
        commands=(
            ShellCommand(id="open-board", label="Open board", url="/boards"),
            ShellCommand(
                id="delete-workspace",
                label="Delete workspace",
                url="/settings/delete",
                required_permissions=frozenset({Permission.WORKSPACE_DELETE}),
            ),
        ),
        notifications=(own_notification, foreign_recipient, foreign_workspace),
        notification_events_url="/notifications/events",
        commands_url="/commands",
    )


def shell_app() -> App:
    return App(config=AppConfig(template_dir=workspace_templates_dir()))


def test_resource_activity_and_notification_contracts_are_immutable_and_validated() -> None:
    workspace_id = WorkspaceId("workspace-one")
    reference = resource(workspace_id)
    event = activity(workspace_id, UserId("user-one"))
    notification = Notification(
        id=NotificationId("notification-one"),
        workspace_id=workspace_id,
        user_id=UserId("user-one"),
        activity_id=event.id,
        sequence=1,
        title="Card changed",
        body="The title changed.",
        url="/boards/cards/one",
        created_at=STAMP,
    )

    with pytest.raises(FrozenInstanceError):
        reference.url = "/tampered"  # type: ignore[misc]
    with pytest.raises(TypeError):
        event.metadata["new"] = "value"  # type: ignore[index]
    assert event.to_payload()["resource"] == reference.to_payload()
    assert notification.is_read is False

    with pytest.raises(ValidationError, match="application-local"):
        ResourceReference(
            workspace_id=workspace_id,
            product="board",
            kind="card",
            id=ResourceId("card-one"),
            url="https://example.test/card-one",
        )
    with pytest.raises(ValidationError, match="unsafe path"):
        ResourceReference(
            workspace_id=workspace_id,
            product="board",
            kind="card",
            id=ResourceId("card-one"),
            url="/\\evil.example/card-one",
        )
    with pytest.raises(ValidationError, match="same workspace"):
        ActivityEvent(
            id=ActivityId("activity-bad-tenant"),
            workspace_id=workspace_id,
            actor_user_id=None,
            action="board.card.updated",
            resource=resource(WorkspaceId("workspace-two")),
            occurred_at=STAMP,
        )
    with pytest.raises(ValidationError, match="secret"):
        ActivityEvent(
            id=ActivityId("activity-secret"),
            workspace_id=workspace_id,
            actor_user_id=None,
            action="board.card.updated",
            resource=reference,
            occurred_at=STAMP,
            metadata={"access_token": "must-not-persist"},
        )
    with pytest.raises(ValidationError, match="secret"):
        ActivityEvent(
            id=ActivityId("activity-api-key"),
            workspace_id=workspace_id,
            actor_user_id=None,
            action="board.card.updated",
            resource=reference,
            occurred_at=STAMP,
            metadata={"api_key": "must-not-persist"},
        )
    with pytest.raises(ValidationError, match="sequence"):
        Notification(
            id=NotificationId("notification-zero"),
            workspace_id=workspace_id,
            user_id=UserId("user-one"),
            activity_id=event.id,
            sequence=0,
            title="Card changed",
            body="The title changed.",
            url="/boards/cards/one",
            created_at=STAMP,
        )


async def test_activity_repository_isolates_tenants_users_replay_and_mark_read(
    activity_fixture: ActivityFixture,
) -> None:
    fixture = activity_fixture
    first = activity(fixture.workspace.id, fixture.owner.id, suffix="one")
    second = activity(fixture.workspace.id, fixture.owner.id, suffix="two")
    foreign = activity(
        fixture.other_workspace.id,
        fixture.other_owner.id,
        suffix="foreign",
    )
    first_notifications = await fixture.repository.record(
        first,
        notifications=(draft(fixture.owner.id), draft(fixture.member.id)),
    )
    second_notifications = await fixture.repository.record(
        second,
        notifications=(draft(fixture.owner.id, suffix="two"),),
    )
    await fixture.repository.record(
        foreign,
        notifications=(draft(fixture.other_owner.id, suffix="foreign"),),
    )

    assert {
        event.id
        for event in await fixture.repository.list_activity(workspace_id=fixture.workspace.id)
    } == {first.id, second.id}
    assert await fixture.repository.list_activity(workspace_id=fixture.other_workspace.id) == (
        foreign,
    )
    owner_first = first_notifications[0]
    owner_second = second_notifications[0]
    assert await fixture.repository.notifications_after(
        workspace_id=fixture.workspace.id,
        user_id=fixture.owner.id,
        after_sequence=owner_first.sequence,
    ) == (owner_second,)
    assert await fixture.repository.notifications_after(
        workspace_id=fixture.workspace.id,
        user_id=fixture.member.id,
    ) == (first_notifications[1],)
    assert (
        await fixture.repository.notifications_after(
            workspace_id=fixture.other_workspace.id,
            user_id=fixture.owner.id,
        )
        == ()
    )
    with pytest.raises(ValidationError, match="non-negative"):
        await fixture.repository.notifications_after(
            workspace_id=fixture.workspace.id,
            user_id=fixture.owner.id,
            after_sequence=-1,
        )

    read = await fixture.repository.mark_read(
        workspace_id=fixture.workspace.id,
        user_id=fixture.owner.id,
        notification_id=owner_first.id,
    )
    assert read.is_read is True
    assert (
        await fixture.repository.mark_read(
            workspace_id=fixture.workspace.id,
            user_id=fixture.owner.id,
            notification_id=owner_first.id,
        )
        == read
    )
    with pytest.raises(NotFoundError, match="not visible"):
        await fixture.repository.mark_read(
            workspace_id=fixture.workspace.id,
            user_id=fixture.member.id,
            notification_id=owner_first.id,
        )
    with pytest.raises(NotFoundError, match="not visible"):
        await fixture.repository.mark_read(
            workspace_id=fixture.other_workspace.id,
            user_id=fixture.owner.id,
            notification_id=owner_first.id,
        )
    with pytest.raises(ConflictError, match="already exists"):
        await fixture.repository.record(first)
    with pytest.raises(ValidationError, match="each recipient once"):
        await fixture.repository.record(
            activity(fixture.workspace.id, fixture.owner.id, suffix="duplicates"),
            notifications=(draft(fixture.owner.id), draft(fixture.owner.id)),
        )
    with pytest.raises(ValidationError, match="current workspace member"):
        await fixture.repository.record(
            activity(fixture.workspace.id, fixture.owner.id, suffix="foreign-recipient"),
            notifications=(draft(fixture.other_owner.id),),
        )
    with pytest.raises(ValidationError, match="actor must be a current member"):
        await fixture.repository.record(
            activity(fixture.workspace.id, fixture.other_owner.id, suffix="foreign-actor")
        )


async def test_activity_and_notification_cursor_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "activity-restart.db"
    database = Database(f"sqlite:///{path}")
    await database.connect()
    await migrate(database, migration_directory())
    identities = WorkspaceRepository(database)
    result = await identities.bootstrap_first_owner(
        expected_setup_token="setup-token",
        supplied_setup_token="setup-token",
        email="owner@example.test",
        display_name="Owner",
        password=PASSWORD,
        workspace_slug="restart",
        workspace_name="Restart Workspace",
    )
    repository = ActivityRepository(database)
    event = activity(result.workspace.id, result.user.id, suffix="restart")
    projected = await repository.record(
        event,
        notifications=(draft(result.user.id, suffix="restart"),),
    )
    await database.disconnect()

    restarted = Database(f"sqlite:///{path}")
    await restarted.connect()
    try:
        replay = await migrate(restarted, migration_directory())
        durable = ActivityRepository(restarted)
        assert replay.applied == []
        assert await durable.list_activity(workspace_id=result.workspace.id) == (event,)
        assert (
            await durable.notifications_after(
                workspace_id=result.workspace.id,
                user_id=result.user.id,
                after_sequence=projected[0].sequence - 1,
            )
            == projected
        )
    finally:
        await restarted.disconnect()


async def test_stream_honors_last_event_id_and_reauthorizes_each_poll(
    activity_fixture: ActivityFixture,
) -> None:
    fixture = activity_fixture
    first = await fixture.repository.record(
        activity(fixture.workspace.id, fixture.owner.id, suffix="one"),
        notifications=(draft(fixture.owner.id),),
    )
    second = await fixture.repository.record(
        activity(fixture.workspace.id, fixture.owner.id, suffix="two"),
        notifications=(draft(fixture.owner.id, suffix="two"),),
    )
    await fixture.repository.record(
        activity(fixture.workspace.id, fixture.owner.id, suffix="three"),
        notifications=(draft(fixture.owner.id, suffix="three"),),
    )
    authorization_checks = 0

    async def authorize(user_id: UserId, workspace_id: WorkspaceId) -> bool:
        nonlocal authorization_checks
        assert user_id == fixture.owner.id
        assert workspace_id == fixture.workspace.id
        authorization_checks += 1
        return authorization_checks <= 2

    stream = notification_event_stream(
        shell_app(),
        fixture.repository,
        user_id=fixture.owner.id,
        workspace_id=fixture.workspace.id,
        authorize=authorize,
        last_event_id=str(first[0].sequence),
        poll_interval=0.001,
        heartbeat_interval=1.0,
    )
    try:
        replayed = await anext(stream.generator)
        assert replayed.id == str(second[0].sequence)
        assert second[0].title in replayed.data
        with pytest.raises(AuthorizationError, match="changed during delivery"):
            await anext(stream.generator)
        assert authorization_checks == 3
    finally:
        await stream.generator.aclose()

    assert parse_last_event_id(str(first[0].sequence)) == first[0].sequence
    assert parse_last_event_id("tampered") == 0
    assert parse_last_event_id("-1") == 0


def test_shell_filters_permissions_and_renders_progressive_full_page() -> None:
    principal = shell_principal(role=Role.ADMIN)
    context = shell_context(principal)
    foreign_notification = replace(
        shell_notification(principal),
        id=NotificationId("notification-other-user"),
        user_id=UserId("other-user"),
    )
    with pytest.raises(ValidationError, match="authorized user"):
        replace(context, notifications=(foreign_notification,))
    html = shell_app().render(Template("workspace_core/shell.html", shell=context))

    assert "<!doctype html>" in html
    assert 'id="workspace-main"' in html
    assert 'href="/boards"' in html
    assert "Workspace settings" not in html
    assert "Delete workspace" not in html
    assert "Open board" in html
    assert 'href="/commands"' in html
    assert 'href="/boards/cards/one"' in html
    assert 'href="#workspace-main"' in html
    assert 'href="/_workspace-core/shell.css"' in html
    assert 'src="/_workspace-core/shell.js"' in html
    assert 'hx-boost="true"' in html
    assert context.unread_notification_count == 1
    assert "Private recipient notification" not in html
    assert "Private workspace notification" not in html


async def test_htmx_notification_fragment_and_oob_rendering() -> None:
    principal = shell_principal()
    notification = shell_notification(principal)
    app = shell_app()

    @app.route("/fragment")
    def fragment():
        return notification_fragment(notification)

    @app.route("/oob")
    def oob():
        return notification_oob(notification)

    async with TestClient(app) as client:
        fragment_response = await client.get("/fragment", headers={"HX-Request": "true"})
        oob_response = await client.get("/oob", headers={"HX-Request": "true"})

    assert fragment_response.status == 200
    assert "<!doctype html>" not in fragment_response.text
    assert 'data-notification-id="notification-shell"' in fragment_response.text
    assert oob_response.status == 200
    assert 'id="workspace-notification-list"' in oob_response.text
    assert 'hx-swap-oob="afterbegin"' in oob_response.text


async def test_product_template_negotiates_full_page_and_htmx_shell_depth() -> None:
    principal = shell_principal()
    context = shell_context(principal)
    app = App(
        config=AppConfig(
            template_dir=Path(__file__).parent / "templates",
            component_dirs=(workspace_templates_dir(),),
        )
    )

    @app.route("/product")
    def product():
        return Page(
            "product.html",
            "workspace_content",
            page_block_name="workspace_main",
            shell=context,
            message="Mounted without copying shell templates.",
        )

    async with TestClient(app) as client:
        full = await client.get("/product")
        narrow = await client.get("/product", headers={"HX-Request": "true"})
        boosted = await client.get("/product", headers={"HX-Request": "true", "HX-Boosted": "true"})

    assert full.status == 200
    assert "<!doctype html>" in full.text
    assert 'id="workspace-sidebar"' in full.text
    assert 'id="product-content"' in full.text
    assert narrow.status == 200
    assert narrow.text.strip() == (
        '<p id="product-content">Mounted without copying shell templates.</p>'
    )
    assert boosted.status == 200
    assert "<!doctype html>" not in boosted.text
    assert 'id="workspace-main"' in boosted.text
    assert 'id="product-content"' in boosted.text


async def test_registered_shell_assets_are_cacheable_and_contain_enhancements() -> None:
    app = App()
    register_shell_assets(app)

    async with TestClient(app) as client:
        css = await client.get("/_workspace-core/shell.css")
        javascript = await client.get("/_workspace-core/shell.js")

    headers = {name.lower(): value for name, value in css.headers}
    assert css.status == 200
    assert "text/css" in css.content_type
    assert headers["cache-control"] == "public, max-age=3600"
    assert ".workspace-skip-link" in css.text
    assert "prefers-reduced-motion" in css.text

    js_headers = {name.lower(): value for name, value in javascript.headers}
    assert javascript.status == 200
    assert "text/javascript" in javascript.content_type
    assert js_headers["cache-control"] == "public, max-age=3600"
    assert "data-workspace-command-open" in javascript.text
    assert "htmx:afterSwap" in javascript.text
    assert "chirp:sse:disconnected" in javascript.text
