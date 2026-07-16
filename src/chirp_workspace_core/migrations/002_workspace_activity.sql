-- Durable product activity and per-user notification projections.
-- PostgreSQL remains the source of truth; Redis is not required for one replica.

CREATE TABLE workspace_core_activity_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    actor_user_id TEXT,
    action TEXT NOT NULL,
    resource_product TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_url TEXT NOT NULL,
    resource_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    CONSTRAINT uq_workspace_core_activity_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT ck_workspace_core_activity_resource_version CHECK (resource_version >= 1),
    CONSTRAINT ck_workspace_core_activity_schema_version CHECK (schema_version >= 1),
    CONSTRAINT fk_workspace_core_activity_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspace_core_workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_activity_actor FOREIGN KEY (actor_user_id)
        REFERENCES workspace_core_users (id) ON DELETE SET NULL
);

CREATE INDEX ix_workspace_core_activity_workspace_occurred
    ON workspace_core_activity_events (workspace_id, occurred_at, id);

CREATE INDEX ix_workspace_core_activity_resource
    ON workspace_core_activity_events (
        workspace_id, resource_product, resource_kind, resource_id, occurred_at
    );

CREATE TABLE workspace_core_notification_sequence (
    id INTEGER PRIMARY KEY,
    next_value INTEGER NOT NULL,
    CONSTRAINT ck_workspace_core_notification_sequence_singleton CHECK (id = 1),
    CONSTRAINT ck_workspace_core_notification_sequence_positive CHECK (next_value >= 1)
);

INSERT INTO workspace_core_notification_sequence (id, next_value) VALUES (1, 1);

CREATE TABLE workspace_core_notifications (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    sequence INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    schema_version INTEGER NOT NULL,
    CONSTRAINT uq_workspace_core_notifications_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT uq_workspace_core_notifications_activity_user
        UNIQUE (workspace_id, activity_id, user_id),
    CONSTRAINT ck_workspace_core_notifications_sequence CHECK (sequence >= 1),
    CONSTRAINT ck_workspace_core_notifications_schema_version CHECK (schema_version >= 1),
    CONSTRAINT fk_workspace_core_notifications_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspace_core_workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_notifications_user FOREIGN KEY (user_id)
        REFERENCES workspace_core_users (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_notifications_membership FOREIGN KEY (workspace_id, user_id)
        REFERENCES workspace_core_memberships (workspace_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_notifications_activity FOREIGN KEY (workspace_id, activity_id)
        REFERENCES workspace_core_activity_events (workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX ix_workspace_core_notifications_recipient_sequence
    ON workspace_core_notifications (workspace_id, user_id, sequence);

CREATE INDEX ix_workspace_core_notifications_unread
    ON workspace_core_notifications (workspace_id, user_id, read_at, sequence);
