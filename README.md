# AgentFlow

AgentFlow is a deterministic workflow platform. The browser dashboard supports secure sign-in, role-aware workflow management, immutable workflow versions, durable execution, approvals, run/step/attempt inspection, and administrator audit history. It works without an AI service.

## Local setup

Requirements: Python 3.11+, Node.js 20.9+, and npm. SQLite is the local default; no database service is needed.

```sh
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
cd apps/web && npm ci && cd ../..
alembic upgrade head
```

### Start a disposable local instance

To avoid using or migrating the repository's `agentflow.db`, point every backend command at a disposable database. In a terminal from the repository root:

```sh
export DATABASE_URL=sqlite:////tmp/agentflow-phase6.db
alembic upgrade head
python -m agentflow.admin bootstrap-admin
```

The bootstrap command creates the initial administrator interactively. Use the same database URL in each backend terminal. Start the API:

```sh
python -m agentflow
```

Start the background worker in another terminal:

```sh
export DATABASE_URL=sqlite:////tmp/agentflow-phase6.db
python -m agentflow.worker
```

Start the dashboard in a third terminal (the frontend API URL is read at startup):

```sh
cd apps/web && npm run dev
```

Open <http://localhost:3000>. The API health endpoint is <http://localhost:8001/health>; interactive API docs are at <http://localhost:8001/docs>. Run `alembic upgrade head` (or `make migrate`) before starting the API. Application startup checks the current schema revision and fails with a migration command if it is behind; it does not create or reset database tables. Alembic and the API read the same `DATABASE_URL` setting; the frontend's `NEXT_PUBLIC_API_URL` must point to the same API port.

After sign-in, use the left navigation to move between Dashboard, Workflows, Runs, Approvals, and (for administrators) Audit history and Accounts. The Dashboard summarizes accessible workflows and recent persisted runs. Its **Run Text Transformation** tutorial creates or reuses the example workflow, runs it through the real queue and worker, and saves the output; it does not create a duplicate on each dashboard visit. You can skip the example and build your own workflow at **Workflows → Create workflow**.

The visual builder asks for a name, optional description, and ordered action steps. Choose from the API's registered action catalog, configure its schema-backed fields, and reorder or remove steps. The builder supports typed workflow inputs, schema-driven controls, step-output and workflow-input references with JSON Pointer paths, and conditional branches. The preview shows the current sequence and generated version 1 definition. **Advanced: view JSON** is a read-only view of the compatible definition; ordinary creation and editing do not require JSON. The server remains authoritative for validation. Editing a saved workflow creates a new immutable version, so earlier runs continue to reference the definition they used.

The registered action catalog now includes structured values, text and JSON processing, field extraction, validation, comparisons, filtering, totals, outbound HTTP/webhooks, approvals, and final result construction. The visual builder discovers these action schemas from the authenticated API rather than maintaining its own catalog. See [docs/actions.md](docs/actions.md) for the full action contracts, reference syntax, conditional behavior, examples, and outbound HTTP policy. Start a saved workflow from its list row or detail page. The run detail page shows its saved version, status, timestamps, step inputs and outputs, safe errors, and persisted attempt/retry history. Runs are saved by the API and worker, so refreshing the page retains their results. Authorized users can cancel eligible runs. Sensitive configured actions remain blocked until an eligible approver records a decision; the approval page shows its workflow, run, action, and safe inputs. Administrators can inspect structured audit events and create operator, approver, or admin accounts. Workflow deletion is not supported by the API and is therefore not exposed in the dashboard.

The bootstrap command prompts for username, email, and a password twice without echoing it. It accepts no password arguments, refuses to proceed when an active administrator exists, and does not create a default credential. Sign in through the dashboard, then create operator or approver accounts from the administrator panel. There is no public registration route. For recovery, a local OS operator may run `python -m agentflow.admin reset-admin-password ADMIN_USERNAME`; this prompts interactively and revokes that administrator's sessions. Protect the database file and restrict local shell access.

Session cookies are opaque, stored only as hashes, `HttpOnly`, and `SameSite=Lax`. `SESSION_COOKIE_SECURE` defaults to `true`; set it to `false` only for local HTTP development (the example `.env` does this). Enable it behind HTTPS in every deployed environment. Browser state-changing requests require an allowed `Origin` and a session-bound CSRF token. Set `CORS_ORIGINS` to the exact dashboard origins. Sessions expire after `SESSION_TTL_HOURS` (12 by default); sign-out and password changes revoke sessions.

Roles are `admin`, `operator`, and `approver`. Administrators manage accounts and access all records. Operators create and manage their own workflows and runs. Approvers can see pending requests and their action/input context, but cannot edit workflows or run/cancel jobs. A requester cannot approve their own run. Existing Phase 1–4 workflows remain accessible to administrators; their new owner is left null so they are not silently assigned to a user.

## Workflow format

Definitions use schema version 1 and contain 1–50 uniquely identified steps. Inputs are validated against an allowlisted action's Pydantic schema. A legacy `$ref` reads the `value` output from an earlier step; a structured `$ref` selects a JSON Pointer path and `$input` reads a declared workflow input. See [docs/actions.md](docs/actions.md) for full schema rules and examples.

```json
{
  "version": 1,
  "name": "Greeting",
  "steps": [
    { "id": "source", "action": "set_value", "inputs": { "value": "hello" } },
    { "id": "shout", "action": "uppercase", "inputs": { "value": { "$ref": "source" } } }
  ]
}
```

`GET /api/actions` returns each registered action's schema, permissions, timeout, retry, replay, and approval policy. `ACTION_TIMEOUTS` may override action timeouts with a JSON object, for example `{"uppercase":2}`; each value must be greater than 0 and at most 300 seconds. Actions run in separate processes so a timed-out or cancelled action can be terminated. Pure local actions use bounded retry policies; external HTTP actions are one-attempt. Definitions are limited to 50 total action/branch steps and 512 KiB. Unknown actions, extra fields, malformed or forward references, duplicate IDs, statically incompatible references, invalid inputs, oversized steps, and oversized definitions are rejected. Workflows can accept typed run inputs, use prior-step JSON Pointer paths, and branch conditionally; branch history includes skipped steps and persisted decisions. Each workflow has immutable numbered definition versions. `POST /api/workflows/{id}/runs` queues the current version; `POST /api/workflows/{id}/versions/{version}/runs` queues a selected older version. Both return HTTP 202 with the persisted run, and accept an optional `Idempotency-Key` header to deduplicate a caller's repeated start request for that workflow. `POST /api/runs/{id}/cancel` cancels queued work or requests cancellation of a running action. Workflow reads retain the compatible top-level `definition` field. `PUT /api/workflows/{id}` creates a new version rather than changing definitions attached to prior runs. A run pins its workflow version. Run responses include step summaries and persisted attempt history.

### Background worker and recovery

Run records and queue rows are stored in the same configured database and committed atomically. Start the API and a separate worker process; the worker continuously claims durable jobs using expiring, fenced leases:

```sh
python -m agentflow.worker
# or
make worker
```

If no worker is running, runs remain queued. If a worker stops after claiming work, another worker can reclaim the job after its lease expires and resume from completed steps. Each attempt and retry time is persisted. Pure local actions can be replayed under their bounded retry policy. Outbound HTTP and webhook actions are configured for one attempt and are not replayed automatically because a timeout may occur after the receiver has acted. Do not interpret this system as providing exactly-once external execution. If the database is unavailable, the API does not accept the run; an accepted queued run is never reported successful until the worker persists completion. Configure the API and worker with the same `DATABASE_URL` and `ACTION_TIMEOUTS` values.

Configure approval-required registered actions with `APPROVAL_REQUIRED_ACTIONS`, for example `APPROVAL_REQUIRED_ACTIONS=["append"]`. The default is empty for configured actions; `send_webhook` and `require_approval` always require approval. The worker checks a persisted approval bound to the run, step, action, workflow version, and resolved input hash before it starts an attempt. Approval requests expire after `APPROVAL_TTL_HOURS` (24 by default); approvals are single-decision and self-approval is denied. `GET /api/approvals` exposes only the caller's requests, requests pending for approvers, or all requests for admins. `GET /api/audit` is administrator-only and supports bounded filters and pagination.

`MAX_REQUEST_BODY_BYTES` defaults to 1 MiB; JSON nesting is limited to 32 levels; workflow definitions remain limited to 512 KiB and 50 steps; run creation is limited to `MAX_RUNS_PER_HOUR` (100 by default); audit and approval pages are capped at 100 rows. Login attempts are throttled by normalized identity and client address. API responses, audit details, and diagnostic boundaries recursively redact common credential fields and recognizable token patterns. Execution inputs and outputs are retained for deterministic workflow behavior and may contain user-provided values; do not place passwords, API keys, or other credentials in workflow payloads. Audit rows are append-only through the application, not cryptographically immutable; database administrators can modify them. See [docs/security.md](docs/security.md) for the threat model and deployment limits.

### Database migration

Alembic manages schema changes. For a fresh SQLite database run `alembic upgrade head`. The Phase 1 schema is upgraded in place while preserving its workflows and execution history. Back up the database file before upgrading an existing installation, for example `cp agentflow.db agentflow.db.bak`; migrations never delete or reset the database. The current revision is `d142f6a8c903`, following `c8e2a47b5d19`, `f4c2e1a91b07`, `6ab9d731c2f0`, `d9156bc2a041`, `70b823ceda15`, and `0432ed0b773b`. Downgrade is intentionally disabled because it would discard queue, approval, session, and audit history. Legacy workflow owners remain null and are visible to administrators only.

Set `HTTP_ALLOWED_HOSTS` to exact destination hostnames and `HTTP_ALLOWED_PORTS` to explicit ports before using outbound actions. The default allowlist is empty. Wildcards are rejected. Production destinations require HTTPS and public DNS addresses; redirects are not followed. The API data example needs a host configured on the server. See [docs/actions.md](docs/actions.md) and [docs/security.md](docs/security.md).

## Checks

```sh
pytest
ruff check agentflow tests
ruff format --check agentflow tests
cd apps/web && npm run lint && npm run typecheck && npm run format:check && npm run build
cd apps/web && npm run test:e2e
```

The browser journey requires the repository `.venv`, installed web dependencies, Google Chrome at `/usr/bin/google-chrome` (or `PLAYWRIGHT_CHROME_PATH`), and the isolated ports 13000 and 18001. It creates a fresh temporary SQLite database, migrates it through the current Alembic head, seeds only a test administrator, then starts the API, frontend, and worker as needed. It does not use `agentflow.db` or the local development services. Successful runs remove their temporary files; failed runs retain the database and Playwright artifacts under `/tmp/agentflow-phase6-e2e-*` for diagnosis.

## Architecture and next steps

`workflows.py` owns the definition contract, `registry.py` the fixed action catalog, `execution.py` durable submission, `worker.py` lease/retry/recovery coordination, `auth.py` opaque server-validated sessions, `authorization.py` resource ownership checks, `models.py` persistent records, and `main.py` the HTTP boundary. The Next.js dashboard talks to the API only. SQLAlchemy isolates persistence behind `DATABASE_URL`; the database-backed queue keeps local development service-free. AI workflow proposals remain future work.
