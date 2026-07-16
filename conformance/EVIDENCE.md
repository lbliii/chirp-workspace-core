# Workspace Core conformance evidence plan

Issue: [lbliii/chirp#772](https://github.com/lbliii/chirp/issues/772)

This plan is written before cloud resources are created. Workspace Core is a
shared application package, not a fifth Railway marketplace product. The live
proof may use a private or draft disposable template blueprint, but #772 must
not publish a standalone Core marketplace listing or retain a Core demo that
competes with Board, Docs, Chat, or integrated Workspace.

## Proof roles

| Role | Owner and boundary |
| --- | --- |
| Source | A reviewed, public `lbliii/chirp-workspace-core` release tag and its exact lock. Source never points at `main`, a path dependency, or a private repository. |
| Clean proof | A new, empty, disposable Railway project linked from a separate temporary directory. It receives the proof blueprint with no variable overrides. |
| Public application | The clean-proof deployment receives temporary public networking for HTTP/browser evidence. There is no retained Core marketplace demo; downstream product templates own durable demos. Removal remains approval-gated. |

## Acceptance matrix

| Surface | Action or transition | Expected authorization and storage effect | Proof method | Lifecycle state |
| --- | --- | --- | --- | --- |
| Clean clone | Clone the release tag, run locked sync, tests, type/lint/format checks, and build | No unpublished or local dependency enters the build | Fresh temporary clone receipt and wheel inspection | Before Railway |
| Package install | Install the built wheel into clean Python 3.14 | Templates, assets, migrations, and typed exports are present | Clean-venv import, render, and resource enumeration | Before Railway |
| App startup | Start the conformance app with generated secrets and PostgreSQL | Migrations run before readiness; no demo credential or silent seed exists | Local production smoke, then Railway deployment logs/status | Initial deploy |
| Zero-input variables | Deploy the proof blueprint without prompts or overrides | `DATABASE_URL` remains `${{Postgres.DATABASE_URL}}`; application secrets use generated expressions | Serialized blueprint audit without resolved values | Before clean deploy |
| Liveness/readiness | GET and HEAD `/health` and `/ready` | Liveness does not require DB; readiness requires connected compatible schema | Local client plus public HTTP receipt | Initial deploy and restart |
| Assets/full page | Load favicon, shell CSS/JS, setup/login, and authenticated shell | Assets are public; tenant content is absent before authorization | HTTP assertions and desktop/mobile browser screenshots | Initial deploy |
| First owner | Submit one setup claim with the generated setup token | Exactly one owner/workspace is committed; replay fails closed | Browser/HTTP transition and database-backed receipt without recording the token | Initial deploy |
| Authentication | Login, logout, and reuse a signed session | Only the current local identity and session version are trusted | Browser and no-JavaScript form paths | Initial deploy |
| Tenant isolation | Tamper workspace, activity, notification, and membership identifiers | Foreign existence/content is not disclosed and no foreign row changes | HTTP, HTMX, repository, and browser assertions | Initial deploy |
| HTMX/no JavaScript | Navigate and create representative activity with and without enhancement | Both paths enforce the same permission and persist the same event | Full-page, narrow/boosted block, normal form, and OOB receipts | Initial deploy |
| SSE/reconnect | Disconnect after an event ID, reconnect with `Last-Event-ID`, then revoke membership | Only missed recipient events replay; no event emits after current authorization is lost | Stream parser/browser receipt plus durable sequence query | Initial deploy |
| PostgreSQL identity | Exercise identity, membership, activity, and notification writes | Newly provisioned PostgreSQL is the sole durable authority | Version query and service-reference audit without credentials | Initial deploy |
| Restart/shutdown | Create state, restart the web service, then reconnect | Graceful shutdown reaches a terminal state; users/workspaces/events survive | Railway deployment/service IDs and post-restart browser assertions | Restart |
| Database unavailable | Make PostgreSQL unavailable to the proof app | `/health` remains live, `/ready` returns 503, protected data paths fail without leakage | Local fault injection; Railway disposable-service fault after approval | Failure injection |
| Migration failure | Run a deliberately failing migration against a disposable database | Deployment/pre-deploy step fails and app never becomes ready | Local disposable DB and isolated Railway deployment after approval | Failure injection |
| Schema mismatch | Present an incomplete or drifted Core schema | Readiness and repositories emit actionable schema diagnostics | Local disposable DB; no mutation of retained proof data | Failure injection |
| Secret rotation | Rotate the signing secret and retry an old cookie | Existing session is rejected; durable tenant data is unchanged | Two local app instances, then Railway variable/deploy transition after approval | Update |
| Compatible update | Deploy the next compatible Core build over existing data | Forward migrations apply once and existing state remains readable | Release/tag/deployment IDs plus state assertions | Roll forward |
| Rollback | Redeploy the declared compatible prior tag | Prior app starts only while its schema range is compatible; no migration is reversed | Deployment ID and state assertions | Rollback |
| Ejection | Eject the proof source to a user-approved repository and push a harmless change | Ejected source builds independently; no secret or hidden dependency transfers | Separate approval, repository URL/commit, automatic deployment receipt | Ejection |
| Cost/topology | Inspect services, replicas, variables, and volumes | Exactly two Railway services: one web replica running one Pounce serving process, plus PostgreSQL with exactly one PG volume; no Redis/background-worker/email/storage | Railway JSON/metadata assertions without resolved values | Every live state |
| Multi-replica trigger | Record the scale condition rather than provisioning it | Redis is required only for measured multi-process SSE fan-out, shared limits, cache, or job transport | README/conformance receipt; `Acceptance #772: n/a` for live multi-replica proof | Documentation |
| Public marketplace | Do not publish Core as a standalone listing | Board/Docs/Chat/Workspace remain the only product templates and demos | Decision #764 plus public template search/readback | No-impact boundary |

## Local receipt — 2026-07-16

The pre-cloud proof used Python 3.14.3, released Chirp 0.10.2, Core
0.1.0a1, and the locked project environment. It contained no resolved
production credential.

- Focused HTTP and Chromium conformance suite: 9 passed.
- Full suite: 33 passed with 86.28% Core coverage.
- `uv run ruff check .` and `uv run ruff format . --check`: passed.
- `uv run ty check src/ conformance/`: passed.
- `chirp check conformance.app:app --deploy`: all clear with a simulated
  Railway public domain.
- `pounce check --app conformance.app:app --worker-mode async --port 8127`:
  app import, configuration, and port checks passed.
- A real one-worker Pounce process returned 200 for GET `/health`, GET
  `/ready`, and HEAD `/ready`; the HEAD response carried `content-length: 5`
  with no response body. SIGTERM released the listener within three seconds.
- A fresh Python 3.14 virtual environment installed the built wheel plus
  released Chirp 0.10.2, then imported Core and enumerated all six packaged
  CSS, JavaScript, migration, template, and typing resources.
- The real Chromium proof loaded local htmx 2.0.10 before the official SSE
  extension 2.2.4, opened the recipient stream, and swapped a notification.

The first real-server attempt also proved why the conformance baseline must
declare one Pounce serving process: the host-sized automatic process count was
unsuitable for the disposable SQLite smoke. The application now freezes `workers=1`;
Railway acceptance still uses PostgreSQL.

## Approval gates

The following are not authorized by local implementation alone and require an
explicit user approval immediately before execution:

- creating or mutating Railway projects, services, templates, domains,
  variables, volumes, or deployments;
- publishing a package, GitHub release, tag intended for public consumption,
  or any marketplace record;
- creating an ejection repository or pushing an ejection proof commit; and
- deleting or unpublishing any proof resource.

Receipts record only public identifiers, statuses, timestamps, non-secret
configuration expressions, and validation results. Resolved credentials never
enter logs, issues, commits, screenshots, or template metadata.
