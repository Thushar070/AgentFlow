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

Registered actions: `set_value`, `uppercase`, and `append`. `GET /api/actions` returns each action's input/output JSON schemas, required permissions, and timeout policy. The built-in actions are local and deterministic, require no permissions, and have bounded inputs; their policy does not impose a wall-clock timeout or interrupt a running handler. Definitions are limited to 50 steps and 512 KiB. Unknown actions, extra fields, malformed or forward references, duplicate IDs, invalid inputs, oversized steps, and oversized definitions are rejected. Each workflow has immutable numbered definition versions. `POST /api/workflows/{id}/runs` executes the current version; `POST /api/workflows/{id}/versions/{version}/runs` explicitly executes an older version. Workflow reads retain the compatible top-level `definition` field. `PUT /api/workflows/{id}` creates a new version rather than changing definitions attached to prior runs. A run pins its workflow version and persists the execution and all step outcomes in one transaction. Run responses include a derived step-count summary and failure category; failed actions stop later steps, and database commit failures roll back that run. Execution remains synchronous in the API request. This version does not queue work, retry, cancel, authenticate users, or isolate tenants; do not expose it as a shared production service.

### Database migration

Alembic manages schema changes. For a fresh SQLite database run `alembic upgrade head`. The Phase 1 schema is upgraded in place while preserving its workflows and execution history. Back up the database file before upgrading an existing installation, for example `cp agentflow.db agentflow.db.bak`; migrations never delete or reset the database. The current revision is `d9156bc2a041`, following `70b823ceda15` and `0432ed0b773b`. Downgrade is intentionally disabled because converting immutable version history back to the Phase 1 schema would discard data.

## Checks

```sh
pytest
ruff check agentflow tests
ruff format --check agentflow tests
cd apps/web && npm run lint && npm run typecheck && npm run format:check && npm run build
```

## Architecture and next steps

`agentflow/workflows.py` owns the definition contract, `registry.py` the fixed action catalog, `execution.py` the sequential coordinator, `models.py` persistent records, and `main.py` the HTTP boundary. `migrations/versions/` contains the Alembic schema history. The Next.js dashboard talks to the API only. SQLAlchemy isolates persistence behind `DATABASE_URL`; SQLite keeps local development service-free. Background jobs, authentication/authorization, audit events, advanced retry/recovery, and AI workflow proposals remain future work.
