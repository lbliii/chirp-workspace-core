# Chirp Workspace Core

Application-owned tenancy, identity, membership, invitation, and role-based
authorization for Chirp Board, Docs, Chat, and the integrated Workspace.

This package is not part of Chirp's framework API and is not a standalone
marketplace product. It consumes released Chirp contracts and supplies one
versioned application foundation to focused Workspace product repositories.

The first implementation is tracked by
[Chirp issue #770](https://github.com/lbliii/chirp/issues/770). Architecture and
ownership are frozen in
[decision #764](https://github.com/lbliii/chirp/issues/764).

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format . --check
uv run ty check src/
```

## Security boundary

Workspace is the tenant. Every tenant-owned repository operation requires a
workspace identifier, and authorization resolves a current membership before
reading or mutating tenant data. Local users satisfy Chirp's existing user
loader contract; Workspace permissions remain request/workspace scoped.

No production default credential, email provider, Redis service, external
identity provider, or framework API change is introduced here.

## Application integration

```python
from chirp import App
from chirp.data import Database, migrate

from chirp_workspace_core import WorkspaceRepository, chirp_auth_config
from chirp_workspace_core.migrations import migration_directory

database = Database("postgresql://...")
await database.connect()
await migrate(database, migration_directory())

identities = WorkspaceRepository(database)
app = App(db=database)
auth = chirp_auth_config(identities)
```

The application adds Chirp's existing session and authentication middleware
with that `AuthConfig`. A loaded `WorkspaceUser` proves local identity only;
before accessing tenant data, handlers resolve a `WorkspacePrincipal` using
both `user_id` and the route's `workspace_id`.

`bootstrap_first_owner()` consumes an application-supplied setup token once.
`issue_invitation()` and `issue_password_reset()` return plaintext tokens only
to the caller while persisting SHA-256 digests. Applications must deliver those
tokens manually or through an explicitly configured provider and must never
write them to logs, templates, activity, or audit metadata.

## Persistence contract

Core owns only the `workspace_core_*` tables in its packaged migration. Product
repositories own their own namespaced tables. Migrations are append-only and
checksum-protected by Chirp; rollback means deploying a schema-compatible prior
application version, not silently reversing data migrations.

All tenant reads and mutations include `workspace_id`. Role or membership
changes and password resets increment the affected user's `session_version`,
which Chirp's existing authentication middleware uses to invalidate stale
signed-cookie sessions.
