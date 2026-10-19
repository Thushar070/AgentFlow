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
```

Start the API and dashboard in separate terminals:

```sh
python -m agentflow
```

```sh
cd apps/web && npm run dev
```

Open <http://localhost:3000>. The API health endpoint is <http://localhost:8000/health>; interactive API docs are at <http://localhost:8000/docs>. The API creates `agentflow.db` on first startup. Set `DATABASE_URL` to a SQLAlchemy-supported URL to use another database.

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

Registered actions: `set_value`, `uppercase`, and `append`. Unknown actions, extra fields, malformed references, duplicate IDs, invalid inputs, and oversized step lists are rejected. The engine executes steps sequentially in the API request and persists each step outcome and run status. A failed step stops the run. This first version does not queue work, retry, cancel, authenticate users, or isolate tenants; do not expose it as a shared production service.

## Checks

```sh
pytest
ruff check agentflow tests
ruff format --check agentflow tests
cd apps/web && npm run lint && npm run typecheck && npm run format:check && npm run build
```

## Architecture and next steps

`agentflow/workflows.py` owns the definition contract, `registry.py` the fixed action catalog, `execution.py` the sequential coordinator, `models.py` persistent records, and `main.py` the HTTP boundary. The Next.js dashboard talks to the API only. SQLAlchemy isolates persistence behind `DATABASE_URL`; SQLite keeps local development service-free. The first version uses SQLAlchemy metadata creation rather than Alembic migrations; migration management belongs in the reliability phase before schema evolution is needed. Background jobs, authentication/authorization, audit events, advanced retry/recovery, and AI workflow proposals remain future work.
