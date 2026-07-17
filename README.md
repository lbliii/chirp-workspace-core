# Chirp Workspace Core

Application-owned tenancy, identity, role-based authorization, activity,
notifications, and product shell for Chirp Board, Docs, Chat, and the integrated
Workspace.

This package is not part of Chirp's framework API and is not a standalone
marketplace product. It consumes released Chirp contracts and supplies one
versioned application foundation to focused Workspace product repositories.

Identity/RBAC and shell/activity delivery are tracked by
[Chirp issue #770](https://github.com/lbliii/chirp/issues/770) and
[Chirp issue #765](https://github.com/lbliii/chirp/issues/765). Architecture and ownership are frozen in
[decision #764](https://github.com/lbliii/chirp/issues/764).

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format . --check
uv run ty check src/ conformance/
uv build
```

## Release

Core releases are immutable public package artifacts. A `v*` tag whose value matches the version
in `pyproject.toml` builds the sdist and wheel once, attaches them to a GitHub release, and deploys
the same files to the public flat index at `https://lbliii.github.io/chirp-workspace-core/`.
No PyPI account, private registry, Git dependency, or long-lived publication token is required.

## Conformance consumer

`conformance.app` is the executable proof consumer for
[issue #772](https://github.com/lbliii/chirp/issues/772). It exercises the
released Core package through a production-shaped Chirp application so local
and disposable Railway checks can cover migrations, readiness, authentication,
tenant isolation, HTMX, normal forms, OOB updates, SSE reconnect, restart, and
compatible update or rollback behavior.

The consumer is evidence infrastructure. It is not another Workspace product,
a starter repository, a retained public demo, or a Railway marketplace listing.
Board, Docs, Chat, and the integrated Workspace remain the independently
deployable products.

The conformance shell serves pinned local copies of htmx 2.0.10 and the
official htmx SSE extension 2.2.4. Their license files ship beside the assets;
the public proof does not depend on a browser-time CDN fetch.

The application requires exactly these values:

| Variable | Requirement |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL for acceptance proof. SQLite may be used only for a quick local smoke. |
| `WORKSPACE_SETUP_TOKEN` | Application-generated first-owner claim token containing at least 24 characters. |
| `CHIRP_SECRET_KEY` | Signing secret containing at least 32 characters. Rotating it invalidates existing signed sessions. |

Railway supplies `PORT` and `RAILWAY_PUBLIC_DOMAIN`; they are platform inputs,
not additional application secrets. Set `CHIRP_ENV=production` for the local
deploy-contract check. The values below are intentionally local-only examples:

```bash
export DATABASE_URL='sqlite:///workspace-core-conformance.db'
export WORKSPACE_SETUP_TOKEN='local-only-workspace-setup-token'
export CHIRP_SECRET_KEY='local-only-workspace-secret-key-00000000'
export CHIRP_ENV='production'
export RAILWAY_PUBLIC_DOMAIN='127.0.0.1'

uv run python -m conformance.migrate
PYTHONPATH=. uv run chirp check conformance.app:app --deploy
uv run python -m conformance.app
```

For #772 acceptance, replace the SQLite URL with a disposable PostgreSQL URL,
then verify both liveness and database/schema readiness:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl --fail --head http://127.0.0.1:8000/ready
```

Do not commit, print, or copy resolved production secrets into logs, evidence,
screenshots, template metadata, or issue comments. The full evidence and
approval-gate matrix lives in [`conformance/EVIDENCE.md`](conformance/EVIDENCE.md).

### Railway proof topology

The cost-bounded baseline is exactly two Railway services: one web replica
running one Pounce serving process, plus one managed PostgreSQL service with
exactly one volume mounted at `/var/lib/postgresql/data`. The web service keeps
`DATABASE_URL` as the exact
private reference `${{Postgres.DATABASE_URL}}`; `CHIRP_SECRET_KEY` and
`WORKSPACE_SETUP_TOKEN` use Railway-generated secret expressions. Migrations
run through `uv run python -m conformance.migrate` before the web process starts,
and the proof blueprint sets `CHIRP_SKIP_MIGRATIONS=1` on the web service so
that pre-deploy is the single migration owner. Railway checks `/ready` before
declaring the deployment healthy.

Redis, background-worker services, email, object storage, and extra replicas
are not part of this baseline. Redis becomes eligible only after measured need
for multi-process SSE fan-out, shared rate limits, caching, or a selected job
transport.

Railway proof uses separate source and empty disposable clean-proof projects.
It may use a private or draft proof blueprint and temporary public networking,
but it must not publish Workspace Core to the marketplace, attach a retained
Core demo, or add Core as a catalog product. Creating, mutating, ejecting, or
removing Railway and GitHub proof resources remains an explicit approval gate.

## Security boundary

Workspace is the tenant. Every tenant-owned repository operation requires a
workspace identifier, and authorization resolves a current membership before
reading or mutating tenant data. Local users satisfy Chirp's existing user
loader contract; Workspace permissions remain request/workspace scoped.

No production default credential, email provider, Redis service, external
identity provider, or framework API change is introduced here.

## Application integration

```python
from chirp import App, AppConfig
from chirp.data import Database, migrate

from chirp_workspace_core import (
    WorkspaceRepository,
    chirp_auth_config,
    register_shell_assets,
    workspace_templates_dir,
)
from chirp_workspace_core.migrations import migration_directory

database = Database("postgresql://...")
await database.connect()
await migrate(database, migration_directory())

identities = WorkspaceRepository(database)
app = App(
    config=AppConfig(
        template_dir="templates",
        component_dirs=(workspace_templates_dir(),),
    ),
    db=database,
)
register_shell_assets(app)
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

## Shared shell

Product page templates extend `workspace_core/shell.html` and override its
named `workspace_title`, `workspace_page_title`, `workspace_page_actions`, and
`workspace_content` blocks. Routes pass a `ShellContext` built from the current
`WorkspacePrincipal`; `build_shell_context()` removes navigation and commands
whose exact permission requirements are not present. Route handlers must still
enforce the same permissions—the hidden link is not an authorization boundary.

The shell keeps ordinary links and forms as its no-JavaScript behavior. When a
product loads htmx, boosted navigation swaps the named `workspace_main` block
while the topbar, notification stream, and focus contract remain stable. Core
does not bundle or select an htmx version for consumers. The package asset
routes add only local shell CSS and a small DOM-behavior script; there is no
client router or global state store.

Each product supplies `commands_url` as an ordinary server-rendered command
index/search route. The dialog is an enhancement of those same links, so a
script failure does not remove command access.

Keyboard behavior:

- `Control+K` or `Command+K` opens the command palette.
- `/` opens it when focus is not in an editable control.
- Native dialog `Escape` closes it and restores focus to the invoker.
- After an htmx main-content swap, focus moves to the new page heading.

The shell uses landmarks, a skip link, polite notification announcements,
native dialog behavior, responsive navigation, visible focus, and
`prefers-reduced-motion`. Products remain responsible for the accessibility of
their own content and commands.

## Activity, notifications, and reconnect

`ActivityRepository.record()` appends a versioned `ActivityEvent` and its
recipient-specific `NotificationDraft` projections in one transaction.
Activity is distinct from Core's security audit log. All resource and
notification URLs must be safe application-local paths, metadata is scalar and
secret-bearing keys are rejected, and every read includes workspace and user
scope.

`notification_event_stream()` queries the durable notification table after the
browser's `Last-Event-ID`, renders the shared notification named block, and
emits an `SSEEvent` with the monotonic delivery sequence as its cursor. Its
authorization callback runs before every poll so a role or membership change
closes the stream instead of retaining stale authority. This one-replica
baseline polls PostgreSQL; Redis is introduced only after measured
multi-process fan-out need.

```python
@app.route("/workspaces/{workspace_id}/notifications/events", referenced=True)
def notification_events(request, workspace_id: str):
    user = get_user()
    return notification_event_stream(
        app,
        activity,
        user_id=user.id,
        workspace_id=WorkspaceId(workspace_id),
        authorize=reload_current_membership,
        last_event_id=request.headers.get("last-event-id"),
    )
```

`reload_current_membership` must query current identity/membership state from
the database; it must not close over the request's original principal.

## Persistence contract

Core owns only the `workspace_core_*` tables in its packaged migrations. Product
repositories own their own namespaced tables. Migrations are append-only and
checksum-protected by Chirp; rollback means deploying a schema-compatible prior
application version, not silently reversing data migrations.

All tenant reads and mutations include `workspace_id`. Role or membership
changes and password resets increment the affected user's `session_version`,
which Chirp's existing authentication middleware uses to invalidate stale
signed-cookie sessions.
