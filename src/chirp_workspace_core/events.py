"""Stable cross-product resource, activity, and notification contracts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import NewType

from .errors import ValidationError
from .models import UserId, WorkspaceId

ResourceId = NewType("ResourceId", str)
ActivityId = NewType("ActivityId", str)
NotificationId = NewType("NotificationId", str)

type MetadataValue = str | int | float | bool | None

_SENSITIVE_METADATA_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "connection_string",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "signing_key",
    "token",
    "webhook_key",
)


def _require_text(value: str, surface: str) -> None:
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValidationError(f"{surface} must be non-empty, trimmed text")


def _require_local_url(value: str, surface: str) -> None:
    if not value.startswith("/") or value.startswith("//"):
        raise ValidationError(f"{surface} must be an application-local absolute path")
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValidationError(f"{surface} contains an unsafe path character")


def _freeze_metadata(metadata: Mapping[str, MetadataValue]) -> MappingProxyType[str, MetadataValue]:
    frozen: dict[str, MetadataValue] = {}
    for key, value in metadata.items():
        _require_text(key, "activity metadata key")
        normalized = key.casefold().replace("-", "_")
        if any(part in normalized for part in _SENSITIVE_METADATA_PARTS):
            raise ValidationError(f"activity metadata key {key!r} could contain a secret")
        if not isinstance(value, str | int | float | bool | type(None)):
            raise ValidationError(f"activity metadata value for {key!r} is not scalar")
        if isinstance(value, float) and not isfinite(value):
            raise ValidationError(f"activity metadata value for {key!r} must be finite")
        frozen[key] = value
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class ResourceReference:
    """A product-owned resource identity safe to share across Workspace products."""

    workspace_id: WorkspaceId
    product: str
    kind: str
    id: ResourceId
    url: str
    version: int = 1

    def __post_init__(self) -> None:
        _require_text(str(self.workspace_id), "resource workspace id")
        _require_text(self.product, "resource product")
        _require_text(self.kind, "resource kind")
        _require_text(str(self.id), "resource id")
        _require_local_url(self.url, "resource URL")
        if self.version < 1:
            raise ValidationError("resource version must be at least 1")

    def to_payload(self) -> dict[str, str | int]:
        """Return the versioned, JSON-compatible public shape."""

        return {
            "workspace_id": str(self.workspace_id),
            "product": self.product,
            "kind": self.kind,
            "id": str(self.id),
            "url": self.url,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """An immutable product activity record, separate from security audit events."""

    id: ActivityId
    workspace_id: WorkspaceId
    actor_user_id: UserId | None
    action: str
    resource: ResourceReference
    occurred_at: str
    metadata: Mapping[str, MetadataValue] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_text(str(self.id), "activity id")
        _require_text(str(self.workspace_id), "activity workspace id")
        _require_text(self.action, "activity action")
        _require_text(self.occurred_at, "activity timestamp")
        if self.resource.workspace_id != self.workspace_id:
            raise ValidationError("activity resource must belong to the same workspace")
        if self.schema_version < 1:
            raise ValidationError("activity schema version must be at least 1")
        object.__setattr__(self, "metadata", _freeze_metadata(dict(self.metadata)))

    def to_payload(self) -> dict[str, object]:
        """Return the versioned, JSON-compatible public shape."""

        return {
            "id": str(self.id),
            "workspace_id": str(self.workspace_id),
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "action": self.action,
            "resource": self.resource.to_payload(),
            "occurred_at": self.occurred_at,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class Notification:
    """A per-user delivery projection derived from product activity."""

    id: NotificationId
    workspace_id: WorkspaceId
    user_id: UserId
    activity_id: ActivityId
    sequence: int
    title: str
    body: str
    url: str
    created_at: str
    read_at: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_text(str(self.id), "notification id")
        _require_text(str(self.workspace_id), "notification workspace id")
        _require_text(str(self.user_id), "notification user id")
        _require_text(str(self.activity_id), "notification activity id")
        if self.sequence < 1:
            raise ValidationError("notification sequence must be at least 1")
        _require_text(self.title, "notification title")
        _require_text(self.body, "notification body")
        _require_local_url(self.url, "notification URL")
        _require_text(self.created_at, "notification timestamp")
        if self.read_at is not None:
            _require_text(self.read_at, "notification read timestamp")
        if self.schema_version < 1:
            raise ValidationError("notification schema version must be at least 1")

    @property
    def is_read(self) -> bool:
        """Whether this delivery has been read by its recipient."""

        return self.read_at is not None

    def to_payload(self) -> dict[str, object]:
        """Return the versioned, JSON-compatible public shape."""

        return {
            "id": str(self.id),
            "workspace_id": str(self.workspace_id),
            "user_id": str(self.user_id),
            "activity_id": str(self.activity_id),
            "sequence": self.sequence,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "created_at": self.created_at,
            "read_at": self.read_at,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class NotificationDraft:
    """The recipient-specific fields used to project one activity event."""

    user_id: UserId
    title: str
    body: str
    url: str

    def __post_init__(self) -> None:
        _require_text(str(self.user_id), "notification recipient user id")
        _require_text(self.title, "notification title")
        _require_text(self.body, "notification body")
        _require_local_url(self.url, "notification URL")
