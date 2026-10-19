# AgentFlow

AgentFlow is a deterministic workflow platform. This initial vertical slice lets you define validated, sequential workflows, run a bounded set of registered actions, and inspect persisted step results from a small web dashboard. It works without an AI service.

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

Start the API and dashboard in separate terminals:

```sh
python -m agentflow
```

```sh
cd apps/web && npm run dev
```

Open <http://localhost:3000>. The API health endpoint is <http://localhost:8000/health>; interactive API docs are at <http://localhost:8000/docs>. Run `alembic upgrade head` (or `make migrate`) before starting the API. Application startup checks the current schema revision and fails with a migration command if it is behind; it does not create or reset database tables. Alembic and the API read the same `DATABASE_URL` setting.

## Workflow format

Definitions use schema version 1 and contain 1–50 uniquely identified steps. Inputs are validated against an allowlisted action's Pydantic schema. A `$ref` input reads the `value` output from an earlier step.

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

Registered actions: `set_value`, `uppercase`, and `append`. `GET /api/actions` returns each action's schemas, required permissions, timeout, retry, and replay policy. `ACTION_TIMEOUTS` may override action timeouts with a JSON object, for example `{"uppercase":2}`; each value must be greater than 0 and at most 300 seconds. Actions run in separate processes so a timed-out or cancelled action can be terminated. Each action has a bounded retry count and exponential backoff. Definitions are limited to 50 steps and 512 KiB. Unknown actions, extra fields, malformed or forward references, duplicate IDs, invalid inputs, oversized steps, and oversized definitions are rejected. Each workflow has immutable numbered definition versions. `POST /api/workflows/{id}/runs` queues the current version; `POST /api/workflows/{id}/versions/{version}/runs` queues a selected older version. Both return HTTP 202 with the persisted run, and accept an optional `Idempotency-Key` header to deduplicate a caller's repeated start request for that workflow. `POST /api/runs/{id}/cancel` cancels queued work or requests cancellation of a running action. Workflow reads retain the compatible top-level `definition` field. `PUT /api/workflows/{id}` creates a new version rather than changing definitions attached to prior runs. A run pins its workflow version. Run responses include step summaries and persisted attempt history.

### Background worker and recovery

Run records and queue rows are stored in the same configured database and committed atomically. Start the API and a separate worker process; the worker continuously claims durable jobs using expiring, fenced leases:

```sh
python -m agentflow.worker
# or
make worker
```

If no worker is running, runs remain queued. If a worker stops after claiming work, another worker can reclaim the job after its lease expires and resume from completed steps. Each attempt and retry time is persisted. Repeated delivery can rerun an action whose result was not saved; current built-in actions are pure local operations, so this does not repeat external side effects. External side-effect actions are not supported until they provide an idempotency/reconciliation contract. Do not interpret this system as providing exactly-once external execution. If the database is unavailable, the API does not accept the run; an accepted queued run is never reported successful until the worker persists completion. Configure the API and worker with the same `DATABASE_URL` and `ACTION_TIMEOUTS` values.

This version does not authenticate users or isolate tenants; do not expose it as a shared production service.

### Database migration

Alembic manages schema changes. For a fresh SQLite database run `alembic upgrade head`. The Phase 1 schema is upgraded in place while preserving its workflows and execution history. Back up the database file before upgrading an existing installation, for example `cp agentflow.db agentflow.db.bak`; migrations never delete or reset the database. The current revision is `6ab9d731c2f0`, following `d9156bc2a041`, `70b823ceda15`, and `0432ed0b773b`. Downgrade is intentionally disabled because it would discard queue and attempt history.

## Checks

```sh
pytest
ruff check agentflow tests
ruff format --check agentflow tests
cd apps/web && npm run lint && npm run typecheck && npm run format:check && npm run build
```

## Architecture and next steps

`agentflow/workflows.py` owns the definition contract, `registry.py` the fixed action catalog, `execution.py` durable submission, `worker.py` lease/retry/recovery coordination, `models.py` persistent records, and `main.py` the HTTP boundary. `migrations/versions/` contains the Alembic schema history. The Next.js dashboard talks to the API only. SQLAlchemy isolates persistence behind `DATABASE_URL`; the database-backed queue keeps local development service-free. Authentication/authorization, approval workflows, and AI workflow proposals remain future work.
