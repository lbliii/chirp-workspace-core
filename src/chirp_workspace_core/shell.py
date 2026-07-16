"""Permission-aware data contracts for the shared Workspace shell."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from chirp.app import App
from chirp.http.response import Response
from chirp.templating.returns import OOB, Fragment

from .errors import ValidationError
from .events import ActivityEvent, Notification
from .models import UserId, WorkspaceId, WorkspacePrincipal

_PACKAGE_DIR = Path(__file__).parent


def _require_text(value: str, surface: str) -> None:
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValidationError(f"{surface} must be non-empty, trimmed text")


def _require_local_url(value: str, surface: str) -> None:
    if not value.startswith("/") or value.startswith("//"):
        raise ValidationError(f"{surface} must be an application-local absolute path")
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValidationError(f"{surface} contains an unsafe path character")


@dataclass(frozen=True, slots=True)
class NavigationItem:
    """One application- or product-owned navigation destination."""

    label: str
    url: str
    product: str
    required_permissions: frozenset[str] = frozenset()
    active: bool = False

    def __post_init__(self) -> None:
        _require_text(self.label, "navigation label")
        _require_local_url(self.url, "navigation URL")
        _require_text(self.product, "navigation product")

    def visible_to(self, permissions: frozenset[str]) -> bool:
        """Return whether all exact required permissions are present."""

        return self.required_permissions <= permissions


@dataclass(frozen=True, slots=True)
class WorkspaceChoice:
    """A workspace destination shown only after membership authorization."""

    id: WorkspaceId
    name: str
    url: str
    current: bool = False

    def __post_init__(self) -> None:
        _require_text(str(self.id), "workspace choice id")
        _require_text(self.name, "workspace choice name")
        _require_local_url(self.url, "workspace choice URL")


@dataclass(frozen=True, slots=True)
class Breadcrumb:
    """A location in the current product hierarchy."""

    label: str
    url: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.label, "breadcrumb label")
        if self.url is not None:
            _require_local_url(self.url, "breadcrumb URL")


@dataclass(frozen=True, slots=True)
class ShellCommand:
    """A keyboard-discoverable command backed by a normal link."""

    id: str
    label: str
    url: str
    group: str = "Navigate"
    shortcut: str | None = None
    required_permissions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require_text(self.id, "command id")
        _require_text(self.label, "command label")
        _require_local_url(self.url, "command URL")
        _require_text(self.group, "command group")
        if self.shortcut is not None:
            _require_text(self.shortcut, "command shortcut")

    def visible_to(self, permissions: frozenset[str]) -> bool:
        """Return whether all exact required permissions are present."""

        return self.required_permissions <= permissions


@dataclass(frozen=True, slots=True)
class ShellContext:
    """Secret-free, render-ready context for one authorized workspace request."""

    workspace_id: WorkspaceId
    user_id: UserId
    workspace_name: str
    user_display_name: str
    product_name: str
    primary_navigation: tuple[NavigationItem, ...]
    product_navigation: tuple[NavigationItem, ...]
    workspace_choices: tuple[WorkspaceChoice, ...]
    breadcrumbs: tuple[Breadcrumb, ...]
    commands: tuple[ShellCommand, ...]
    notifications: tuple[Notification, ...] = ()
    notification_events_url: str | None = None
    commands_url: str = "/commands"
    asset_prefix: str = "/_workspace-core"

    def __post_init__(self) -> None:
        _require_text(str(self.workspace_id), "shell workspace id")
        _require_text(str(self.user_id), "shell user id")
        _require_text(self.workspace_name, "shell workspace name")
        _require_text(self.user_display_name, "shell user display name")
        _require_text(self.product_name, "shell product name")
        _require_local_url(self.commands_url, "commands URL")
        _require_local_url(self.asset_prefix, "shell asset prefix")
        if self.notification_events_url is not None:
            _require_local_url(self.notification_events_url, "notification events URL")
        if not any(choice.current for choice in self.workspace_choices):
            raise ValidationError("shell workspace choices must identify the current workspace")
        if sum(choice.current for choice in self.workspace_choices) != 1:
            raise ValidationError("shell workspace choices must have exactly one current workspace")
        current = next(choice for choice in self.workspace_choices if choice.current)
        if current.id != self.workspace_id:
            raise ValidationError("current workspace choice must match the authorized workspace")
        for notification in self.notifications:
            if notification.workspace_id != self.workspace_id:
                raise ValidationError("shell notifications must belong to the authorized workspace")
            if notification.user_id != self.user_id:
                raise ValidationError("shell notifications must belong to the authorized user")

    @property
    def unread_notification_count(self) -> int:
        """Count unread deliveries already filtered for this user and workspace."""

        return sum(not notification.is_read for notification in self.notifications)


def build_shell_context(
    principal: WorkspacePrincipal,
    *,
    product_name: str,
    primary_navigation: Iterable[NavigationItem],
    product_navigation: Iterable[NavigationItem] = (),
    workspace_choices: Iterable[WorkspaceChoice],
    breadcrumbs: Iterable[Breadcrumb] = (),
    commands: Iterable[ShellCommand] = (),
    notifications: Iterable[Notification] = (),
    notification_events_url: str | None = None,
    commands_url: str = "/commands",
    asset_prefix: str = "/_workspace-core",
) -> ShellContext:
    """Build a tenant-bound shell context and remove unauthorized destinations."""

    permissions = principal.permissions
    visible_primary = tuple(item for item in primary_navigation if item.visible_to(permissions))
    visible_product = tuple(item for item in product_navigation if item.visible_to(permissions))
    visible_commands = tuple(command for command in commands if command.visible_to(permissions))
    visible_notifications = tuple(
        notification
        for notification in notifications
        if notification.workspace_id == principal.workspace.id
        and notification.user_id == principal.user.id
    )
    return ShellContext(
        workspace_id=principal.workspace.id,
        user_id=principal.user.id,
        workspace_name=principal.workspace.name,
        user_display_name=principal.user.display_name,
        product_name=product_name,
        primary_navigation=visible_primary,
        product_navigation=visible_product,
        workspace_choices=tuple(workspace_choices),
        breadcrumbs=tuple(breadcrumbs),
        commands=visible_commands,
        notifications=visible_notifications,
        notification_events_url=notification_events_url,
        commands_url=commands_url,
        asset_prefix=asset_prefix,
    )


def workspace_templates_dir() -> Path:
    """Return the installed component directory for ``AppConfig.component_dirs``."""

    return _PACKAGE_DIR / "templates"


def notification_fragment(notification: Notification) -> Fragment:
    """Render the shared notification block for SSE or a targeted response."""

    return Fragment(
        "workspace_core/shell.html",
        "workspace_notification",
        notification=notification,
    )


def activity_fragment(event: ActivityEvent) -> Fragment:
    """Render one shared activity item without product-specific template copies."""

    return Fragment(
        "workspace_core/shell.html",
        "workspace_activity_item",
        activity=event,
    )


def notification_oob(notification: Notification) -> OOB:
    """Render one shared notification into the stable shell live region."""

    return OOB(
        Fragment(
            "workspace_core/shell.html",
            "workspace_oob_ack",
            notification=notification,
        ),
        Fragment(
            "workspace_core/shell.html",
            "workspace_notification",
            target="workspace-notification-list",
            swap="afterbegin",
            notification=notification,
        ),
    )


def register_shell_assets(app: App, *, prefix: str = "/_workspace-core") -> None:
    """Register cacheable CSS and JS routes without changing application config."""

    _require_local_url(prefix, "shell asset prefix")
    normalized = prefix.rstrip("/")

    @app.route(f"{normalized}/shell.css", name="workspace_core_shell_css", referenced=True)
    def workspace_core_shell_css() -> Response:
        body = (_PACKAGE_DIR / "assets" / "shell.css").read_text(encoding="utf-8")
        return Response(body, content_type="text/css; charset=utf-8").with_header(
            "Cache-Control", "public, max-age=3600"
        )

    @app.route(f"{normalized}/shell.js", name="workspace_core_shell_js", referenced=True)
    def workspace_core_shell_js() -> Response:
        body = (_PACKAGE_DIR / "assets" / "shell.js").read_text(encoding="utf-8")
        return Response(body, content_type="text/javascript; charset=utf-8").with_header(
            "Cache-Control", "public, max-age=3600"
        )
