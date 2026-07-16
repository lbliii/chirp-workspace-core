"""Durable product activity and notification delivery for Workspace products."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from chirp.app import App
from chirp.data import Database, DatabaseConnectionError, QueryError
from chirp.realtime.events import EventStream, SSEEvent

from .errors import (
    AuthorizationError,
    ConflictError,
    DatabaseUnavailableError,
    NotFoundError,
    SchemaMismatchError,
    ValidationError,
)
from .events import (
    ActivityEvent,
    ActivityId,
    Notification,
    NotificationDraft,
    NotificationId,
    ResourceId,
    ResourceReference,
)
from .models import UserId, WorkspaceId, new_id
from .shell import notification_fragment

_T = TypeVar("_T")
type StreamAuthorizer = Callable[[UserId, WorkspaceId], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class _ActivityRow:
    id: str
    workspace_id: str
    actor_user_id: str | None
    action: str
    resource_product: str
    resource_kind: str
    resource_id: str
    resource_url: str
    resource_version: int
    occurred_at: str
    metadata_json: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class _NotificationRow:
    id: str
    workspace_id: str
    user_id: str
    activity_id: str
    sequence: int
    title: str
    body: str
    url: str
    created_at: str
    read_at: str | None
    schema_version: int


@dataclass(frozen=True, slots=True)
class _RecipientRow:
    user_id: str


class ActivityRepository:
    """Append-only activity log and tenant/user-scoped notification projection."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = new_id,
    ) -> None:
        self._db = database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    def _stamp(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            raise RuntimeError("Workspace Core clock must return a timezone-aware datetime.")
        return now.astimezone(UTC).isoformat(timespec="microseconds")

    @staticmethod
    def _schema_error(exc: QueryError) -> bool:
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in ("no such table", "no column named", "does not exist", "undefined table")
        )

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
                    f"Workspace schema cannot update {surface}; run all packaged Core migrations."
                ) from exc
            raise

    async def _fetch_one(self, cls: type[_T], sql: str, *params: Any, surface: str) -> _T | None:
        try:
            return await self._db.fetch_one(cls, sql, *params)
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                f"Workspace database is unavailable while reading {surface}."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    f"Workspace schema cannot read {surface}; run all packaged Core migrations."
                ) from exc
            raise

    async def _fetch(self, cls: type[_T], sql: str, *params: Any, surface: str) -> list[_T]:
        try:
            return await self._db.fetch(cls, sql, *params)
        except DatabaseConnectionError as exc:
            raise DatabaseUnavailableError(
                f"Workspace database is unavailable while reading {surface}."
            ) from exc
        except QueryError as exc:
            if self._schema_error(exc):
                raise SchemaMismatchError(
                    f"Workspace schema cannot read {surface}; run all packaged Core migrations."
                ) from exc
            raise

    @staticmethod
    def _activity(row: _ActivityRow) -> ActivityEvent:
        metadata = json.loads(row.metadata_json)
        if not isinstance(metadata, dict):
            raise SchemaMismatchError("Workspace activity metadata is not a JSON object.")
        return ActivityEvent(
            id=ActivityId(row.id),
            workspace_id=WorkspaceId(row.workspace_id),
            actor_user_id=UserId(row.actor_user_id) if row.actor_user_id else None,
            action=row.action,
            resource=ResourceReference(
                workspace_id=WorkspaceId(row.workspace_id),
                product=row.resource_product,
                kind=row.resource_kind,
                id=ResourceId(row.resource_id),
                url=row.resource_url,
                version=row.resource_version,
            ),
            occurred_at=row.occurred_at,
            metadata=metadata,
            schema_version=row.schema_version,
        )

    @staticmethod
    def _notification(row: _NotificationRow) -> Notification:
        return Notification(
            id=NotificationId(row.id),
            workspace_id=WorkspaceId(row.workspace_id),
            user_id=UserId(row.user_id),
            activity_id=ActivityId(row.activity_id),
            sequence=row.sequence,
            title=row.title,
            body=row.body,
            url=row.url,
            created_at=row.created_at,
            read_at=row.read_at,
            schema_version=row.schema_version,
        )

    async def _next_sequence(self) -> int:
        await self._execute(
            "UPDATE workspace_core_notification_sequence "
            "SET next_value = next_value + 1 WHERE id = 1",
            surface="notification sequence",
        )
        row = await self._fetch_one(
            _SequenceRow,
            "SELECT next_value FROM workspace_core_notification_sequence WHERE id = 1",
            surface="notification sequence",
        )
        if row is None:
            raise SchemaMismatchError("Workspace notification sequence singleton is missing.")
        return row.next_value - 1

    async def record(
        self,
        event: ActivityEvent,
        *,
        notifications: Iterable[NotificationDraft] = (),
    ) -> tuple[Notification, ...]:
        """Append one activity event and its per-user projections atomically."""

        drafts = tuple(notifications)
        recipients = {draft.user_id for draft in drafts}
        if len(recipients) != len(drafts):
            raise ValidationError("activity notifications must contain each recipient once")
        created_at = self._stamp()
        projected: list[Notification] = []
        try:
            async with self._db.transaction():
                if event.actor_user_id is not None:
                    actor = await self._fetch_one(
                        _RecipientRow,
                        "SELECT user_id FROM workspace_core_memberships "
                        "WHERE workspace_id = ? AND user_id = ?",
                        str(event.workspace_id),
                        str(event.actor_user_id),
                        surface="activity actor membership",
                    )
                    if actor is None:
                        raise ValidationError(
                            "activity actor must be a current member of the event workspace"
                        )
                for draft in drafts:
                    recipient = await self._fetch_one(
                        _RecipientRow,
                        "SELECT user_id FROM workspace_core_memberships "
                        "WHERE workspace_id = ? AND user_id = ?",
                        str(event.workspace_id),
                        str(draft.user_id),
                        surface="notification recipient membership",
                    )
                    if recipient is None:
                        raise ValidationError(
                            "activity notification recipient must be a current workspace member"
                        )
                await self._execute(
                    "INSERT INTO workspace_core_activity_events "
                    "(id, workspace_id, actor_user_id, action, resource_product, resource_kind, "
                    "resource_id, resource_url, resource_version, occurred_at, metadata_json, "
                    "schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    str(event.id),
                    str(event.workspace_id),
                    str(event.actor_user_id) if event.actor_user_id else None,
                    event.action,
                    event.resource.product,
                    event.resource.kind,
                    str(event.resource.id),
                    event.resource.url,
                    event.resource.version,
                    event.occurred_at,
                    json.dumps(dict(event.metadata), sort_keys=True, separators=(",", ":")),
                    event.schema_version,
                    surface="product activity",
                )
                for draft in drafts:
                    sequence = await self._next_sequence()
                    notification = Notification(
                        id=NotificationId(self._id_factory()),
                        workspace_id=event.workspace_id,
                        user_id=draft.user_id,
                        activity_id=event.id,
                        sequence=sequence,
                        title=draft.title,
                        body=draft.body,
                        url=draft.url,
                        created_at=created_at,
                    )
                    await self._execute(
                        "INSERT INTO workspace_core_notifications "
                        "(id, workspace_id, user_id, activity_id, sequence, title, body, url, "
                        "created_at, read_at, schema_version) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        str(notification.id),
                        str(notification.workspace_id),
                        str(notification.user_id),
                        str(notification.activity_id),
                        notification.sequence,
                        notification.title,
                        notification.body,
                        notification.url,
                        notification.created_at,
                        notification.read_at,
                        notification.schema_version,
                        surface="notification projection",
                    )
                    projected.append(notification)
        except QueryError as exc:
            message = str(exc).casefold()
            if "unique" in message or "duplicate" in message:
                raise ConflictError(
                    "Workspace activity event or notification already exists."
                ) from exc
            raise
        return tuple(projected)

    async def list_activity(
        self, *, workspace_id: WorkspaceId, limit: int = 100
    ) -> tuple[ActivityEvent, ...]:
        """Return recent activity from one explicit tenant only."""

        bounded = min(max(limit, 1), 500)
        rows = await self._fetch(
            _ActivityRow,
            "SELECT id, workspace_id, actor_user_id, action, resource_product, resource_kind, "
            "resource_id, resource_url, resource_version, occurred_at, metadata_json, "
            "schema_version "
            "FROM workspace_core_activity_events WHERE workspace_id = ? "
            "ORDER BY occurred_at DESC, id DESC LIMIT ?",
            str(workspace_id),
            bounded,
            surface="product activity",
        )
        return tuple(self._activity(row) for row in rows)

    async def notifications_after(
        self,
        *,
        workspace_id: WorkspaceId,
        user_id: UserId,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[Notification, ...]:
        """Return recipient-scoped deliveries after a monotonic reconnect cursor."""

        if after_sequence < 0:
            raise ValidationError("notification cursor must be a non-negative integer")
        bounded = min(max(limit, 1), 500)
        rows = await self._fetch(
            _NotificationRow,
            "SELECT id, workspace_id, user_id, activity_id, sequence, title, body, url, "
            "created_at, read_at, schema_version FROM workspace_core_notifications "
            "WHERE workspace_id = ? AND user_id = ? AND sequence > ? "
            "ORDER BY sequence ASC LIMIT ?",
            str(workspace_id),
            str(user_id),
            after_sequence,
            bounded,
            surface="notification replay",
        )
        return tuple(self._notification(row) for row in rows)

    async def mark_read(
        self,
        *,
        workspace_id: WorkspaceId,
        user_id: UserId,
        notification_id: NotificationId,
    ) -> Notification:
        """Mark one exact tenant/recipient delivery read without revealing foreign IDs."""

        read_at = self._stamp()
        changed = await self._execute(
            "UPDATE workspace_core_notifications SET read_at = ? "
            "WHERE id = ? AND workspace_id = ? AND user_id = ? AND read_at IS NULL",
            read_at,
            str(notification_id),
            str(workspace_id),
            str(user_id),
            surface="notification read state",
        )
        if changed == 0:
            row = await self._notification_row(workspace_id, user_id, notification_id)
            if row is None:
                raise NotFoundError("Workspace notification is not visible to this recipient.")
            return self._notification(row)
        row = await self._notification_row(workspace_id, user_id, notification_id)
        if row is None:
            raise NotFoundError("Workspace notification disappeared after update.")
        return self._notification(row)

    async def _notification_row(
        self, workspace_id: WorkspaceId, user_id: UserId, notification_id: NotificationId
    ) -> _NotificationRow | None:
        return await self._fetch_one(
            _NotificationRow,
            "SELECT id, workspace_id, user_id, activity_id, sequence, title, body, url, "
            "created_at, read_at, schema_version FROM workspace_core_notifications "
            "WHERE id = ? AND workspace_id = ? AND user_id = ?",
            str(notification_id),
            str(workspace_id),
            str(user_id),
            surface="notification delivery",
        )


@dataclass(frozen=True, slots=True)
class _SequenceRow:
    next_value: int


def parse_last_event_id(raw: str | None) -> int:
    """Validate an opaque browser reconnect cursor; invalid input starts fresh."""

    if raw is None:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value >= 0 else 0


def notification_event_stream(
    app: App,
    repository: ActivityRepository,
    *,
    user_id: UserId,
    workspace_id: WorkspaceId,
    authorize: StreamAuthorizer,
    last_event_id: str | None = None,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
) -> EventStream:
    """Create a durable HTML notification stream with permission rechecks."""

    if poll_interval <= 0:
        raise ValidationError("notification poll interval must be positive")
    cursor = parse_last_event_id(last_event_id)

    async def generate() -> AsyncIterator[SSEEvent]:
        nonlocal cursor
        while True:
            if not await authorize(user_id, workspace_id):
                raise AuthorizationError("Notification stream authorization is no longer current.")
            notifications = await repository.notifications_after(
                workspace_id=workspace_id,
                user_id=user_id,
                after_sequence=cursor,
            )
            for notification in notifications:
                if not await authorize(user_id, workspace_id):
                    raise AuthorizationError(
                        "Notification stream authorization changed during delivery."
                    )
                html = app.render(notification_fragment(notification))
                yield SSEEvent(data=html, id=str(notification.sequence))
                cursor = notification.sequence
            await asyncio.sleep(poll_interval)

    return EventStream(generate(), heartbeat_interval=heartbeat_interval)
