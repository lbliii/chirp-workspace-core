"""Production-shaped consumer proving Workspace Core through released Chirp APIs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from chirp.data import Database
from chirp.middleware.auth import AuthMiddleware
from chirp.middleware.csrf import CSRFMiddleware
from chirp.middleware.security_headers import SecurityHeadersConfig, SecurityHeadersMiddleware
from chirp.middleware.sessions import SessionConfig, SessionMiddleware

from chirp_workspace_core import (
    ActivityEvent,
    ActivityId,
    ActivityRepository,
    AuthorizationError,
    Breadcrumb,
    NavigationItem,
    NotFoundError,
    NotificationDraft,
    Permission,
    ResourceId,
    ResourceReference,
    ShellCommand,
    UserId,
    ValidationError,
    WorkspaceChoice,
    WorkspaceCoreError,
    WorkspaceId,
    WorkspacePrincipal,
    WorkspaceRepository,
    activity_fragment,
    build_shell_context,
    chirp_auth_config,
    new_id,
    notification_event_stream,
    register_shell_assets,
    workspace_templates_dir,
)
from chirp_workspace_core.migrations import migration_directory

if TYPE_CHECKING:
    from chirp.app import App
    from chirp.config import AppConfig
    from chirp.health import HealthCheck
    from chirp.http.request import Request
    from chirp.http.response import Redirect, Response
    from chirp.middleware.auth import get_user, login, logout
    from chirp.templating.returns import OOB, Fragment, Page, Template
else:
    # Runtime deliberately exercises Chirp's released, lazy public import surface.
    from chirp import (
        OOB,
        App,
        AppConfig,
        Fragment,
        HealthCheck,
        Page,
        Redirect,
        Request,
        Response,
        Template,
        get_user,
        login,
        logout,
    )

APP_ROOT = Path(__file__).parent
TEMPLATES = APP_ROOT / "templates"
STATIC = APP_ROOT / "static"


@dataclass(frozen=True, slots=True)
class ConformanceSettings:
    """Application-owned settings that deliberately do not extend ``AppConfig``."""

    database_url: str = field(repr=False)
    setup_token: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", self.database_url),
                ("WORKSPACE_SETUP_TOKEN", self.setup_token),
                ("CHIRP_SECRET_KEY", self.secret_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Workspace Core conformance requires: " + ", ".join(missing))
        if len(self.setup_token) < 24:
            raise RuntimeError("WORKSPACE_SETUP_TOKEN must contain at least 24 characters")
        if len(self.secret_key) < 32:
            raise RuntimeError("CHIRP_SECRET_KEY must contain at least 32 characters")

    @classmethod
    def from_env(cls) -> ConformanceSettings:
        """Load required application settings without exposing resolved values."""

        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            setup_token=os.environ.get("WORKSPACE_SETUP_TOKEN", ""),
            secret_key=os.environ.get("CHIRP_SECRET_KEY", ""),
        )


@dataclass(frozen=True, slots=True)
class ConformanceRuntime:
    """Testable bundle for the runnable conformance consumer."""

    app: App
    database: Database = field(repr=False)
    identities: WorkspaceRepository = field(repr=False)
    activity: ActivityRepository = field(repr=False)


def _form_text(form: object, name: str) -> str:
    getter = getattr(form, "get", None)
    value = getter(name, "") if callable(getter) else ""
    return value if isinstance(value, str) else ""


def create_conformance_runtime(
    settings: ConformanceSettings | None = None,
    *,
    config: AppConfig | None = None,
) -> ConformanceRuntime:
    """Build the conformance app without creating external state."""

    resolved = settings or ConformanceSettings.from_env()
    app_config = config or AppConfig.from_env(
        template_dir=TEMPLATES,
        component_dirs=(workspace_templates_dir(),),
        static_dir=STATIC,
        secret_key=resolved.secret_key,
        csp_nonce_enabled=True,
        strict_transport_security="max-age=86400",
        workers=1,
        worker_mode="async",
    )
    database = Database(resolved.database_url)
    app = App(
        config=app_config,
        db=database,
        migrations=str(migration_directory()),
    )
    identities = WorkspaceRepository(database)
    activities = ActivityRepository(database)

    app.add_middleware(
        SecurityHeadersMiddleware(
            SecurityHeadersConfig(
                content_security_policy=None,
                strict_transport_security=app_config.strict_transport_security,
            )
        )
    )
    app.add_middleware(SessionMiddleware(SessionConfig(secret_key=resolved.secret_key)))
    app.add_middleware(AuthMiddleware(chirp_auth_config(identities)))
    app.add_middleware(CSRFMiddleware())
    register_shell_assets(app)

    async def core_schema_ready() -> bool:
        try:
            await identities.bootstrap_state()
        except WorkspaceCoreError:
            return False
        return True

    app.add_health_check(
        HealthCheck(
            "workspace-core-schema",
            check=core_schema_ready,
            message="Workspace Core schema is unavailable or incompatible",
        )
    )

    def public_page(*, mode: str, error: str = "", status: int = 200) -> Response:
        html = app.render(Template("public.html", mode=mode, error=error))
        return Response(html, status=status)

    async def current_principal(workspace_id: str) -> WorkspacePrincipal | None:
        user = get_user()
        if not getattr(user, "is_authenticated", False):
            return None
        try:
            return await identities.principal(
                user_id=str(user.id),
                workspace_id=workspace_id,
            )
        except AuthorizationError, NotFoundError:
            return None

    async def render_workspace(
        principal: WorkspacePrincipal,
        *,
        view: str = "dashboard",
    ) -> Page:
        principals = await identities.principals_for_user(str(principal.user.id))
        notifications = await activities.notifications_after(
            workspace_id=principal.workspace.id,
            user_id=principal.user.id,
            limit=20,
        )
        activity = await activities.list_activity(workspace_id=principal.workspace.id, limit=50)
        base = f"/workspaces/{principal.workspace.id}"
        shell = build_shell_context(
            principal,
            product_name="Core Conformance",
            primary_navigation=(
                NavigationItem("Overview", base, "workspace-core", active=view == "dashboard"),
                NavigationItem(
                    "Commands", f"{base}/commands", "workspace-core", active=view == "commands"
                ),
            ),
            workspace_choices=tuple(
                WorkspaceChoice(
                    candidate.workspace.id,
                    candidate.workspace.name,
                    f"/workspaces/{candidate.workspace.id}",
                    current=candidate.workspace.id == principal.workspace.id,
                )
                for candidate in principals
            ),
            breadcrumbs=(
                Breadcrumb(principal.workspace.name, base),
                Breadcrumb("Commands" if view == "commands" else "Conformance"),
            ),
            commands=(
                ShellCommand("overview", "Open conformance overview", base),
                ShellCommand(
                    "record-check",
                    "Record conformance activity",
                    f"{base}#record-check",
                    required_permissions=frozenset({Permission.CONTENT_WRITE}),
                ),
            ),
            notifications=notifications,
            notification_events_url=f"{base}/notifications/events",
            commands_url=f"{base}/commands",
        )
        return Page(
            "workspace.html",
            "workspace_content",
            page_block_name="workspace_main",
            shell=shell,
            activities=activity,
            can_write=Permission.CONTENT_WRITE in principal.permissions,
            activity_url=f"{base}/activity",
            view=view,
        )

    @app.route("/")
    async def index():
        state = await identities.bootstrap_state()
        if state.completed_at is None:
            return public_page(mode="setup")
        user = get_user()
        if not getattr(user, "is_authenticated", False):
            return public_page(mode="login")
        principals = await identities.principals_for_user(str(user.id))
        if not principals:
            logout()
            return public_page(
                mode="login",
                error="This identity has no current Workspace membership.",
                status=403,
            )
        return Redirect(f"/workspaces/{principals[0].workspace.id}", status=303)

    @app.route("/login")
    async def login_page():
        return public_page(mode="login")

    @app.route("/login", methods=["POST"])
    async def login_submit(request: Request):
        form = await request.form()
        try:
            user = await identities.authenticate(
                _form_text(form, "email"),
                _form_text(form, "password"),
            )
        except ValidationError:
            user = None
        if user is None:
            return public_page(mode="login", error="Invalid email or password.", status=401)
        principals = await identities.principals_for_user(str(user.id))
        if not principals:
            return public_page(
                mode="login",
                error="This identity has no current Workspace membership.",
                status=403,
            )
        login(user)
        return Redirect(f"/workspaces/{principals[0].workspace.id}", status=303)

    @app.route("/setup", methods=["POST"])
    async def setup_submit(request: Request):
        form = await request.form()
        try:
            result = await identities.bootstrap_first_owner(
                expected_setup_token=resolved.setup_token,
                supplied_setup_token=_form_text(form, "setup_token"),
                email=_form_text(form, "email"),
                display_name=_form_text(form, "display_name"),
                password=_form_text(form, "password"),
                workspace_slug=_form_text(form, "workspace_slug"),
                workspace_name=_form_text(form, "workspace_name"),
            )
        except WorkspaceCoreError as exc:
            return public_page(mode="setup", error=str(exc), status=422)
        login(result.user)
        return Redirect(f"/workspaces/{result.workspace.id}", status=303)

    @app.route("/logout", methods=["POST"])
    def logout_submit():
        logout()
        return Redirect("/", status=303)

    @app.route("/workspaces/{workspace_id}")
    async def workspace_page(workspace_id: str):
        principal = await current_principal(workspace_id)
        if principal is None:
            return Response("Not found", status=404, content_type="text/plain; charset=utf-8")
        return await render_workspace(principal)

    @app.route("/workspaces/{workspace_id}/commands")
    async def commands_page(workspace_id: str):
        principal = await current_principal(workspace_id)
        if principal is None:
            return Response("Not found", status=404, content_type="text/plain; charset=utf-8")
        return await render_workspace(principal, view="commands")

    @app.route("/workspaces/{workspace_id}/activity", methods=["POST"])
    async def record_activity(request: Request, workspace_id: str):
        principal = await current_principal(workspace_id)
        if principal is None:
            return Response("Not found", status=404, content_type="text/plain; charset=utf-8")
        try:
            await identities.require_permissions(
                user_id=str(principal.user.id),
                workspace_id=workspace_id,
                permissions=(Permission.CONTENT_WRITE,),
            )
        except AuthorizationError:
            return Response("Forbidden", status=403, content_type="text/plain; charset=utf-8")
        form = await request.form()
        note = _form_text(form, "note").strip()
        if not 1 <= len(note) <= 200:
            return Response(
                "Conformance note must contain 1 to 200 characters.",
                status=422,
                content_type="text/plain; charset=utf-8",
            )
        event_id = new_id()
        event = ActivityEvent(
            id=ActivityId(event_id),
            workspace_id=principal.workspace.id,
            actor_user_id=principal.user.id,
            action="conformance.check.recorded",
            resource=ResourceReference(
                workspace_id=principal.workspace.id,
                product="workspace-core",
                kind="conformance-check",
                id=ResourceId(event_id),
                url=f"/workspaces/{workspace_id}",
            ),
            occurred_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            metadata={"note": note},
        )
        projected = await activities.record(
            event,
            notifications=(
                NotificationDraft(
                    user_id=principal.user.id,
                    title="Conformance activity recorded",
                    body=note,
                    url=f"/workspaces/{workspace_id}",
                ),
            ),
        )
        if request.is_htmx:
            return OOB(
                activity_fragment(event),
                Fragment(
                    "workspace_core/shell.html",
                    "workspace_notification",
                    target="workspace-notification-list",
                    swap="afterbegin",
                    notification=projected[0],
                ),
            )
        return Redirect(f"/workspaces/{workspace_id}", status=303)

    @app.route("/workspaces/{workspace_id}/notifications/events", referenced=True)
    async def notification_events(request: Request, workspace_id: str):
        principal = await current_principal(workspace_id)
        if principal is None:
            return Response("Not found", status=404, content_type="text/plain; charset=utf-8")

        async def authorize(user_id: UserId, authorized_workspace_id: WorkspaceId) -> bool:
            try:
                await identities.principal(
                    user_id=str(user_id),
                    workspace_id=str(authorized_workspace_id),
                )
            except AuthorizationError, NotFoundError:
                return False
            return True

        return notification_event_stream(
            app,
            activities,
            user_id=principal.user.id,
            workspace_id=principal.workspace.id,
            authorize=authorize,
            last_event_id=request.headers.get("last-event-id"),
        )

    return ConformanceRuntime(
        app=app,
        database=database,
        identities=identities,
        activity=activities,
    )
