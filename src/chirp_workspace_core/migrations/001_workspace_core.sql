-- Workspace Core foundation schema.
-- IDs and timestamps are application-generated opaque UUID and ISO-8601 text.
-- The DDL intentionally uses the common SQLite/PostgreSQL subset.

CREATE TABLE workspace_core_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    disabled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT ck_workspace_core_users_session_version CHECK (session_version >= 0),
    CONSTRAINT ck_workspace_core_users_is_active CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_workspace_core_users_disabled_state CHECK (
        (is_active = 1 AND disabled_at IS NULL)
        OR (is_active = 0 AND disabled_at IS NOT NULL)
    )
);

CREATE TABLE workspace_core_workspaces (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workspace_core_memberships (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT uq_workspace_core_memberships_workspace_user UNIQUE (workspace_id, user_id),
    CONSTRAINT uq_workspace_core_memberships_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT ck_workspace_core_memberships_role
        CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    CONSTRAINT fk_workspace_core_memberships_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspace_core_workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_memberships_user FOREIGN KEY (user_id)
        REFERENCES workspace_core_users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_workspace_core_memberships_user_workspace
    ON workspace_core_memberships (user_id, workspace_id);

CREATE TABLE workspace_core_invitations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT,
    invited_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT uq_workspace_core_invitations_workspace_id UNIQUE (workspace_id, id),
    CONSTRAINT ck_workspace_core_invitations_role CHECK (role IN ('admin', 'member', 'viewer')),
    CONSTRAINT ck_workspace_core_invitations_terminal_state CHECK (
        accepted_at IS NULL OR revoked_at IS NULL
    ),
    CONSTRAINT fk_workspace_core_invitations_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspace_core_workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_core_invitations_inviter FOREIGN KEY (invited_by)
        REFERENCES workspace_core_users (id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_workspace_core_invitations_active_email
    ON workspace_core_invitations (workspace_id, normalized_email)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

CREATE INDEX ix_workspace_core_invitations_workspace_created
    ON workspace_core_invitations (workspace_id, created_at);

CREATE TABLE workspace_core_password_resets (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    CONSTRAINT ck_workspace_core_password_resets_terminal_state CHECK (
        used_at IS NULL OR revoked_at IS NULL
    ),
    CONSTRAINT fk_workspace_core_password_resets_user FOREIGN KEY (user_id)
        REFERENCES workspace_core_users (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_workspace_core_password_resets_active_user
    ON workspace_core_password_resets (user_id)
    WHERE used_at IS NULL AND revoked_at IS NULL;

CREATE TABLE workspace_core_audit_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT,
    actor_user_id TEXT,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT fk_workspace_core_audit_events_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspace_core_workspaces (id) ON DELETE SET NULL,
    CONSTRAINT fk_workspace_core_audit_events_actor FOREIGN KEY (actor_user_id)
        REFERENCES workspace_core_users (id) ON DELETE SET NULL
);

CREATE INDEX ix_workspace_core_audit_events_workspace_created
    ON workspace_core_audit_events (workspace_id, created_at);

CREATE INDEX ix_workspace_core_audit_events_subject
    ON workspace_core_audit_events (workspace_id, subject_type, subject_id, created_at);

CREATE TABLE workspace_core_bootstrap (
    id INTEGER PRIMARY KEY,
    completed_at TEXT,
    owner_user_id TEXT,
    workspace_id TEXT,
    CONSTRAINT ck_workspace_core_bootstrap_singleton CHECK (id = 1),
    CONSTRAINT ck_workspace_core_bootstrap_completion CHECK (
        (completed_at IS NULL AND owner_user_id IS NULL AND workspace_id IS NULL)
        OR (completed_at IS NOT NULL AND owner_user_id IS NOT NULL AND workspace_id IS NOT NULL)
    )
);

INSERT INTO workspace_core_bootstrap (id) VALUES (1);
