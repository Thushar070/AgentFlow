# AgentFlow

AgentFlow is a deterministic workflow platform. The browser dashboard supports secure sign-in, role-aware workflow management, immutable workflow versions, durable execution, approvals, run/step/attempt inspection, and administrator audit history. It works without an AI service.

## Repository layout

```text
AgentFlow/
├── src/agentflow/       Python API, workflow engine, and worker package
├── apps/web/            Next.js dashboard
├── migrations/          Alembic database migrations
├── tests/               Backend and browser test suites
├── scripts/             PostgreSQL and FreeLLMAPI setup utilities
├── run.sh               One-command local launcher
├── pyproject.toml       Python package and tool configuration
└── Makefile             Common development commands
```

The `.venv`, `apps/web/node_modules`, and `.local` directories are machine-local dependencies and runtime data; they are ignored by Git. `.local` holds the project PostgreSQL data, logs, and protected pre-migration backup.

## Local setup

Requirements: Python 3.11+, Node.js 20.9+, npm, and PostgreSQL 16 or newer. PostgreSQL is the application database for both the API and worker. SQLite is used only by isolated tests and the one-time migration utility.

```sh
cp .env.example .env
cp apps/web/.env.example apps/web/.env.local
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
cd apps/web && npm ci && cd ../..
```

Create a PostgreSQL role and database for the app before migrating. On Debian/Ubuntu, for example, run `sudo -u postgres createuser --pwprompt agentflow` and `sudo -u postgres createdb --owner=agentflow agentflow`; enter a strong password at the prompt and put the same password in `.env`. Use the same `DATABASE_URL` for Alembic, the API, and the worker. On a single-host deployment, bind PostgreSQL to loopback; for managed PostgreSQL, use the provider's private endpoint and TLS settings.

For a local development setup without system administrator access, run `scripts/start-local-postgres.sh`. It creates an ignored `.local/postgres` data directory, starts PostgreSQL on loopback, creates a dedicated `agentflow` role and database, and writes a random role password to the private root `.env`. The data directory stays inside the workspace and is not committed. Run the same script after a host restart before launching AgentFlow.

Redis is an optional cache for the stable action catalog and AI system prompt. Set `REDIS_URL` in `.env`; `run.sh` starts a loopback Redis server when the configured local endpoint is unavailable and `redis-server` is installed. For a hosted deployment, provide a private managed Redis endpoint separately from Neon Postgres. If Redis is unavailable, the API falls back to generating these values directly. Cache entries expire after five minutes; user prompts and generated workflows are never cached.

### Local Qwen with Ollama (optional)

FreeLLMAPI is the active AI provider. The Qwen choices are hidden in the workflow editor while their generated definitions are being made compatible with AgentFlow validation. Ollama model files can remain installed locally without running a service. To start Ollama for development or direct API checks, use `ENABLE_LOCAL_OLLAMA=1 ./run.sh`; the normal `./run.sh` keeps it stopped.

### Use Neon PostgreSQL

Neon is supported as the managed PostgreSQL database. In this already linked project, run `neon env pull --env DATABASE_URL --env DATABASE_URL_UNPOOLED --file .env` to set up the pooled URL for API and worker traffic plus the direct URL for migrations. Alembic uses the direct URL to avoid transaction-pooler limitations. Neon commonly provides `postgresql://...` URLs; AgentFlow converts both URLs to the Psycopg SQLAlchemy driver automatically. Each API/worker process keeps a bounded SQLAlchemy pool (5 idle, up to 5 burst connections by default), recycles older connections, and checks them before reuse. Tune `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, and `DB_POOL_TIMEOUT_SECONDS` for the deployment's worker count and Neon plan. Keep URLs in `.env` or your deployment platform's secret manager, never in source control. When the configured database host is remote, `run.sh` skips starting the project-local PostgreSQL instance and applies Alembic migrations to the configured Neon database before starting the API and worker. The local PostgreSQL setup script remains for local-only development.

The Neon Function declared in `neon.ts` exposes a read-only database health probe at its invocation URL. Neon provides that URL as `NEON_FUNCTION_HEALTH_BASE_URL`; `run.sh` passes it to the Next.js server, which exposes it through `/api/neon-health`. The Function uses Neon's injected `DATABASE_URL` with a small module-scoped `pg` pool and `attachDatabasePool`.

Switching the connection URL does not copy data from the local PostgreSQL database into Neon. Migrate existing records before switching production traffic, and verify users, workflows, runs, approvals, and audit history in Neon.

```sh
alembic upgrade head
```

### Start the local PostgreSQL instance

From the repository root, run scripts/start-local-postgres.sh, then .venv/bin/python -m alembic upgrade head. For a fresh database, bootstrap the first administrator with .venv/bin/python -m agentflow.admin bootstrap-admin.

The API, worker, and Alembic read the same private root .env. Start the API with .venv/bin/python -m agentflow, the worker with .venv/bin/python -m agentflow.worker in another terminal, and the dashboard with cd apps/web && npm run dev in a third terminal.

For a one-command local launch, run `./run.sh` from the repository root. It prepares missing Python/web dependencies, starts PostgreSQL and local Redis when needed, applies migrations, starts the API, worker, and website, and starts the local FreeLLMAPI Compose service when its API key and Compose project are configured. Local Ollama startup is opt-in with `ENABLE_LOCAL_OLLAMA=1`. Press Ctrl+C to stop services started by the script; PostgreSQL remains available for the next launch. Logs are written under `.local/logs/`.

Open <http://localhost:3000>. The API health endpoint is <http://localhost:8001/health>; interactive API docs are at <http://localhost:8001/docs>. Keep the dashboard and API on the same loopback hostname so the browser sends the session cookie correctly. Run `alembic upgrade head` (or `make migrate`) before starting the API. Application startup checks the current schema revision and fails with a migration command if it is behind; it does not create or reset database tables. Alembic and the API read the same `DATABASE_URL` setting; the frontend's `NEXT_PUBLIC_API_URL` must point to the same API port.

After sign-in, use the left navigation to move between Dashboard, Workflows, Runs, Approvals, and (for administrators) Audit history and Accounts. The Dashboard summarizes accessible workflows and recent persisted runs. Its **Run Text Transformation** tutorial creates or reuses the example workflow, runs it through the real queue and worker, and saves the output; it does not create a duplicate on each dashboard visit. You can skip the example and build your own workflow at **Workflows → Create workflow**.

The visual builder asks for a name, optional description, and ordered action steps. Choose from the API's registered action catalog, configure its schema-backed fields, and reorder or remove steps. The builder supports typed workflow inputs, schema-driven controls, step-output and workflow-input references with JSON Pointer paths, and conditional branches. The preview shows the current sequence and generated version 1 definition. **Advanced: view JSON** is a read-only view of the compatible definition; ordinary creation and editing do not require JSON. The server remains authoritative for validation. Editing a saved workflow creates a new immutable version, so earlier runs continue to reference the definition they used.

The registered action catalog includes structured values, text and JSON processing, field extraction, validation, comparisons, filtering, totals, outbound HTTP/webhooks, approvals, and final result construction. The visual builder discovers action schemas from the authenticated API. Start a saved workflow from its list row or detail page. The run detail page shows its saved version, status, timestamps, step inputs and outputs, safe errors, and persisted attempt/retry history. Runs are saved by the API and worker, so refreshing the page retains their results. Authorized users can cancel eligible runs. Sensitive configured actions remain blocked until an eligible approver records a decision; the approval page shows its workflow, run, action, and safe inputs. Administrators can inspect structured audit events and create operator, approver, or admin accounts. Workflow deletion is not supported by the API and is therefore not exposed in the dashboard.

### Optional FreeLLMAPI workflow proposals

AgentFlow can draft a workflow from natural language in **Workflows → Create workflow**. The backend sends the prompt and current registered action catalog to the local FreeLLMAPI OpenAI-compatible gateway. The returned proposal uses the existing workflow schema, then appears in the visual builder for review and editing. Generating a proposal does not create a workflow or run. Saving is an explicit action; starting a saved workflow is a separate explicit action. The non-AI builder and execution engine continue to work when FreeLLMAPI is stopped or absent.

Install FreeLLMAPI using its official Docker Compose instructions. It keeps provider credentials encrypted in its own persistent SQLite volume and binds the dashboard/API to loopback by default. Read [the official install guide](https://github.com/tashfeenahmed/freellmapi/blob/main/docs/en/install/01-install.md) before setting it up. Add an upstream provider key and obtain the separate unified API key in its dashboard. AgentFlow never downloads models or stores upstream provider credentials.

```sh
cd ~/freellmapi
docker compose up -d
```

Set the backend-only values in `.env`; keep the API key out of frontend configuration and chat:

```dotenv
FREELLMAPI_BASE_URL=http://localhost:3001/v1
FREELLMAPI_API_KEY=<unified-key-from-FreeLLMAPI-dashboard>
FREELLMAPI_MODEL=auto
FREELLMAPI_TIMEOUT_SECONDS=120
```

AgentFlow sends FreeLLMAPI requests through its unified key with the gateway `auto` route, allowing its active fallback chain to choose among enabled providers. Local Qwen selection is disabled. The proposal endpoint uses a non-streaming OpenAI-compatible Chat Completions request with JSON Schema formatting and strict server-side workflow/action/reference validation. It rejects code/file generation and prompts without a supported workflow intent before contacting FreeLLMAPI. Prompts are sent through FreeLLMAPI to whichever upstream provider serves the request; do not include secrets or sensitive personal data.

The bootstrap command prompts for username, email, and a password twice without echoing it. Passwords must be exactly 8 characters and include at least one number and one special symbol; the database stores Argon2id hashes. Bootstrap accepts no password arguments, refuses to proceed when an active administrator exists, and does not create a default credential. Sign in through the dashboard, then create operator or approver accounts from the administrator panel. There is no public registration route. For recovery, a local OS operator may run `python -m agentflow.admin reset-admin-password ADMIN_USERNAME`; this prompts interactively and revokes that administrator's sessions. Protect PostgreSQL credentials, database access, and host shell access. Use a dedicated application role with access only to the AgentFlow database.

The API binds only to loopback; `API_HOST` rejects public and LAN interfaces. `CORS_ORIGINS` accepts only exact localhost/loopback dashboard origins, and trusted-host checks reject other host headers. Session cookies are opaque, stored only as hashes, and `HttpOnly`, `Secure`, and `SameSite=Lax`. Browser state-changing requests require an allowed `Origin` and a session-bound CSRF token. Sessions expire after `SESSION_TTL_HOURS` (12 by default); sign-out and password changes revoke sessions. The API adds clickjacking, MIME-sniffing, referrer, and browser-capability protection headers.

Redis enforces a shared limit of 300 API requests per minute per client address, 20 login requests per minute, and 10 AI proposals per minute / 60 per hour. Login also throttles repeated failures by account and client address; workflow runs have a separate per-user hourly cap. If Redis is unavailable, short-window limits fall back to bounded process-local counters. Keep the API on one local host; do not expose it through a public reverse proxy.

Roles are `admin`, `operator`, and `approver`. Administrators manage accounts and access all records. Operators create and manage their own workflows and runs. Approvers can see pending requests and their action/input context, but cannot edit workflows or run/cancel jobs. A requester cannot approve their own run. Existing Phase 1–4 workflows remain accessible to administrators; their new owner is left null so they are not silently assigned to a user.

## Workflow format

Definitions use schema version 1 and contain 1–50 uniquely identified steps. Inputs are validated against an allowlisted action's Pydantic schema. A legacy `$ref` reads the `value` output from an earlier step; a structured `$ref` selects a JSON Pointer path and `$input` reads a declared workflow input.

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

Alembic manages schema changes. For a fresh PostgreSQL database run `alembic upgrade head`. For a one-time migration from SQLite to PostgreSQL, stop the API and worker, make a protected backup, create an empty PostgreSQL database, point `.env` at it, run `alembic upgrade head`, then run `python scripts/migrate_sqlite_to_postgres.py agentflow.db`. The utility verifies both schemas and revisions, refuses a non-empty target, copies rows transactionally in foreign-key order, and advances PostgreSQL sequences. Verify account sign-in and workflow/run history before removing the old SQLite file. Back up PostgreSQL with `pg_dump`; migrations do not reset data. The current revision is `d142f6a8c903`, following `c8e2a47b5d19`, `f4c2e1a91b07`, `6ab9d731c2f0`, `d9156bc2a041`, `70b823ceda15`, and `0432ed0b773b`. Downgrade is intentionally disabled because it would discard queue, approval, session, and audit history. Legacy workflow owners remain null and are visible to administrators only.

Set `HTTP_ALLOWED_HOSTS` to exact destination hostnames and `HTTP_ALLOWED_PORTS` to explicit ports before using outbound actions. The default allowlist is empty. Wildcards are rejected. Production destinations require HTTPS and public DNS addresses; redirects are not followed. The API data example needs a host configured on the server.

## Checks

```sh
pytest
ruff check src/agentflow tests
ruff format --check src/agentflow tests
cd apps/web && npm run lint && npm run typecheck && npm run format:check && npm run build
cd apps/web && npm run test:e2e
```

The browser journey requires the repository `.venv`, installed web dependencies, Google Chrome at `/usr/bin/google-chrome` (or `PLAYWRIGHT_CHROME_PATH`), and the isolated ports 13000 and 18001. It creates a fresh temporary SQLite database, migrates it through the current Alembic head, seeds only a test administrator, then starts the API, frontend, and worker as needed. It does not use `agentflow.db`, PostgreSQL, or the local development services. Successful runs remove their temporary files; failed runs retain the database and Playwright artifacts under `/tmp/agentflow-phase6-e2e-*` for diagnosis.

## Architecture and next steps

`workflows.py` owns the definition contract, `registry.py` the fixed action catalog, `execution.py` durable submission, `worker.py` lease/retry/recovery coordination, `auth.py` opaque server-validated sessions, `authorization.py` resource ownership checks, `models.py` persistent records, and `main.py` the HTTP boundary. The Next.js dashboard talks to the API only. SQLAlchemy isolates persistence behind `DATABASE_URL`; PostgreSQL remains the shared durable store and queue for the API and worker. Optional Redis caches the stable action catalog and AI system prompt; it is not used for durable or user-specific state. Optional FreeLLMAPI workflow proposals are isolated in `ai_proposals.py`; they return validated definitions for review in the same visual builder and never create or run workflows.

### Deployment layout

The current security profile is intentionally single-machine: the frontend and API bind to loopback, and the API refuses non-loopback configuration. For remote personal access, use a VPN or SSH tunnel to the machine; do not expose port 8001 publicly. Keep PostgreSQL, Redis, and the FreeLLMAPI dashboard private. A public multi-host deployment requires a separate, reviewed network/authentication configuration and is outside this local-only setup.
