# AgentFlow Progress

## Phase 1 — Fresh Project Foundation and Deterministic Workflow Slice

**Status:** COMPLETE (owner-authorized runtime alternative; browser UI unavailable)

### Initial repository and environment

- **Verified:** The repository root was `/home/billy/Data/Projects/AgentFlow`. Before implementation, the intentional fresh checkout contained `docs/plan.md` and `docs/agents.md`, both untracked; no source, manifests, tests, or configured commands were present.
- **Verified at Phase 1 start:** Branch `main`; no commits existed yet. Remote `origin` pointed to `https://github.com/Thushar070/AgentFlow.git`; no upstream was set at that time.
- **Verified:** Python 3.14.7, Node.js 24.19.0, npm 11.19.0, Docker 28.5.2, and Compose 2.40.2 are installed. Access to the Docker socket is denied, so PostgreSQL/Redis services could not be started.
- **Decision:** SQLite is the service-free local persistence default behind SQLAlchemy's `DATABASE_URL` boundary. Workflow execution is synchronous for this first vertical slice. Docker Compose, Redis/Celery, PostgreSQL migrations, authentication, retries, cancellation, and AI are not presented as implemented.
- **Verified:** No `.env` or secrets were present. Example environment files contain placeholders/configuration only.

### Implemented

- Python FastAPI API with validated settings, health/action/workflow/run endpoints, Pydantic workflow and action contracts, SQLAlchemy persistence, and a bounded sequential engine. The allowlisted actions are `set_value`, `uppercase`, and `append`.
- Next.js/TypeScript dashboard that fetches API health, creates workflows from JSON, starts runs, and displays persisted recent workflow and step results.
- Reproducible Python and npm lockfiles, `Makefile`, environment examples, ignore rules, setup instructions, and revised phase documents.
- Backend tests run against a real local Uvicorn process and temporary SQLite databases.

### Verification evidence

- **Passed:** `.venv/bin/python -m pip install -r requirements.lock` and `.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` (exit 0).
- **Passed:** `cd apps/web && npm ci` (exit 0); `npm audit --omit=optional` (0 vulnerabilities).
- **Passed:** `.venv/bin/python -m pytest -q` (7 passed, exit 0).
- **Passed:** `.venv/bin/ruff check agentflow tests` and `.venv/bin/ruff format --check agentflow tests` (exit 0).
- **Passed:** `npm run lint`, `npm run typecheck`, `npm run format:check`, and `npm run build` in `apps/web` (all exit 0; production build prerendered `/`).
- **Passed:** Started API and Next.js dev server. API `GET /health` returned HTTP 200; dashboard `GET /` returned HTTP 200.
- **Passed:** Live API smoke test created a two-step workflow, executed it, returned `{"value":"HELLO AGENTFLOW"}`, and confirmed the run appeared in `GET /api/runs` history.
- **Passed:** Automated negative cases reject unknown actions, malformed definitions, invalid/oversized inputs, duplicate IDs, forward references, and invalid run limits; failed steps are persisted and stop later work.
- **Browser:** CUA reported no browser apps/surfaces and could not create Chrome or in-app browser tabs. Per the owner's explicit authorization, HTTP runtime checks, API-to-database workflow exercise, production build, and the dashboard's successful response were used as the strongest available alternative. No browser interaction is claimed.
- **Passed:** `git diff --check` (exit 0); final source review found no credentials or generated build files to include.

### Limitations and next task

- Synchronous API execution can hold a request open; do not expose this initial slice as a shared production service. No auth/tenant isolation, background queue, timeout/retry/cancel/recovery policy, Alembic migration system, or AI adapter exists yet.
- Docker socket access prevented verification against PostgreSQL/Redis. SQLite is the tested local path; SQLAlchemy allows a database URL to be configured, but production compatibility has not been tested.
- Browser UI flows could not be exercised because no browser surface was available. The implementation was checked via live HTTP, API-level workflows, and production build.
- **Next:** Phase 2 should formalize workflow/persistence domain contracts and introduce schema migrations before expanding execution reliability features.

### Git delivery

- The Phase 1 session could not stage because `.git` was read-only at that time. At Phase 2 start, commit `deaf11485f89cc2df05024443881b0dbcd7a74ea` was present on `main`, and local `origin/main` pointed to the same commit. This session did not push.
- Push status: not pushed by this Phase 2 session; user instruction prohibits automatic push.

## Phase 2 — Persistence Architecture and Database Migrations

**Status:** BLOCKED (implementation and acceptance checks pass; required local commit is blocked by read-only Git metadata)

### Initial findings and safety

- **Verified:** Started from clean `main` at `deaf11485f89cc2df05024443881b0dbcd7a74ea`, matching local tracking ref `origin/main`; Phase 1 commit remains intact.
- **Verified:** Original models embedded the only workflow definition on `Workflow`, had no immutable versions or step ordering, allowed the nominal `queued` status despite synchronous execution, and lacked unique/check constraints. API startup called `Base.metadata.create_all()`; executions committed after each step. There were no migrations.
- **Verified:** Frontend consumes workflow `id`, `name`, and top-level `definition`; run list/detail consume existing `id`, `workflow_id`, `status`, `error`, `started_at`, and step fields. These fields remain available.
- **Backup:** Copied the Phase 1 `agentflow.db` to `/tmp/agentflow-phase2-pre-migration.db` before any migration attempt. It contained one workflow, one successful run, and two successful steps.
- **Migration sequence issue:** The first Alembic attempt was run on the backed-up working DB before disposable-copy tests. It exposed SQLite's deferred DML/revision-stamp behavior and left an empty `workflow_versions` table without data loss. The migration environment was corrected to commit the Alembic revision explicitly and resume this recognized empty-table state. Fresh and legacy-copy tests then passed before the working DB was completed. The backup remained unchanged.
- **Data check:** After upgrade, the working database's workflow IDs/names/definitions, run rows, and step inputs/outputs/errors/timestamps matched the backup. One version 1 was added per workflow; run and step ordering were derived from original insertion order; foreign-key check returned no violations.
- **Actual-copy check:** A separate copy made from `/tmp/agentflow-phase2-pre-migration.db` upgraded through both revisions twice; `alembic check` reported no drift, and its one workflow, one run, two step records, and definition/results matched the pre-migration source.

### Model and migration decisions

- `Workflow` holds stable identity, name, optional description, UTC creation/update timestamps, and a collection of immutable `WorkflowVersion` records.
- `WorkflowVersion.version_number` is unique per workflow. Existing Phase 1 definitions become version 1. Updates create a new version. Existing workflow GET/list responses retain top-level `definition`; additional version metadata is additive.
- Runs pin both workflow ID and exact version ID. A composite FK enforces that the version belongs to the same workflow. The existing run route executes the current version; `/api/workflows/{id}/versions/{version}/runs` explicitly runs a selected older version.
- Synchronous run and step statuses are constrained to `running`, `succeeded`, and `failed`. Application transition rules permit only `running` to a terminal state. Historical `queued`/`running` records migrate to `failed` with an interruption explanation.
- Foreign keys, unique step IDs/orders per run, version uniqueness, status/order checks, lookup indexes, SQLite foreign-key enforcement, and UTC-aware timestamp reads are implemented. The execution service commits the whole run once; DB errors roll it back, while action failures commit their failed-step trace.
- Authentication/ownership is deferred to Phase 5 in accordance with this task's explicit scope restriction; the local API remains single-user and is not safe as a shared service.
- Alembic revisions: `0432ed0b773b` (versioned workflow persistence) and `70b823ceda15` (run input/output and creation time). Downgrade is intentionally disabled to avoid data loss.

### Verification evidence

- **Passed:** `.venv/bin/python -m pip install -r requirements.lock`; `.venv/bin/python -m pip check` (no broken requirements).
- **Passed:** `.venv/bin/python -m pytest -q` — 14 passed. Tests cover fresh and legacy migrations, repeat upgrades and revision tracking, data preservation, interrupted runs, model relationships, UTC timestamps, foreign-key/unique/status constraints, transition rules, atomic rollback, API compatibility, version updates, old-version execution, successful execution, and failed-step persistence.
- **Passed:** `.venv/bin/ruff check agentflow tests migrations`; `.venv/bin/ruff format --check agentflow tests migrations`.
- **Passed:** `npm run lint`, `npm run typecheck`, `npm run format:check`, and `npm run build` in `apps/web`.
- **Passed:** Fresh SQLite DB and a disposable copy of the pre-migration Phase 1 DB upgraded through `alembic upgrade head`; repeated upgrade was a no-op; both recorded `70b823ceda15`.
- **Passed:** Working DB `alembic upgrade head` twice, `alembic check` reported no new operations, and `alembic current` returned `70b823ceda15 (head)`.
- **Passed:** Live API started against `/tmp/agentflow-phase2-runtime.db`, a separate migrated copy. It exposed existing history, accepted create/update, executed current and explicit old versions, returned their real results, retained run history, and emitted the configured CORS origin. Dashboard `GET /` returned HTTP 200.
- **Passed (browser, Chrome):** Opened `http://localhost:3000/` against the API and a disposable `/tmp/agentflow-phase2-browser.db` upgraded through Alembic. Created `Welcome workflow`, ran its two sequential steps, and observed `SUCCEEDED` with both step outputs in dashboard history. Refreshed the page and confirmed the workflow and run history remained visible. Submitted a workflow with an unregistered action and confirmed the dashboard displayed `unknown action: not_registered` without adding a workflow. Created and ran a schema-valid workflow whose referenced integer caused the `uppercase` step to fail at runtime; the dashboard displayed `FAILED`, the completed first step, and the failed step's validation error. The failed execution appeared in history. No browser console inspection capability was exposed by the connected browser interface.
- **Passed:** `git diff --check` (exit 0).

### Final audit — 2026-10-19

- **Independently rerun:** `.venv/bin/python -m pytest -q` — 14 passed in 13.34s. This covers the fresh-database migration/repeat-upgrade case, a populated Phase 1-shaped migration fixture, integrity constraints, state transitions, transaction rollback, API regressions, and successful/failed execution persistence.
- **Independently rerun:** `.venv/bin/ruff check agentflow tests migrations` and `.venv/bin/ruff format --check agentflow tests migrations` — both passed.
- **Independently rerun:** frontend `npm run lint`, `npm run typecheck`, and `npm run format:check` passed. The first `npm run build` attempt failed with `Could not parse output from TypeScript's --showConfig.`; an immediate standalone retry passed, compiling successfully and prerendering `/` and `/_not-found`. The failure was not reproducible on retry; no source changes were made to address it.
- **Fresh DB:** `/tmp/agentflow-phase2-final-audit.db` upgraded through `0432ed0b773b` and `70b823ceda15`; `alembic current` reported `70b823ceda15 (head)`.
- **Actual populated Phase 1 backup:** Confirmed `/tmp/agentflow-phase2-pre-migration.db` still exists. Copied it to `/tmp/agentflow-phase2-audit-legacy-20261019.db`, upgraded the copy twice, and compared workflow identity/definition, run identity/status/timestamps/errors, and step identity/order/action/status/inputs/outputs/errors/timestamps. All supported records matched; 1 workflow, 1 run, and 2 steps were preserved; the copy reached `70b823ceda15`; `PRAGMA foreign_key_check` returned zero violations. The original backup was not modified.
- **Migration failure behavior:** On a separate disposable SQLite file containing an unrelated sentinel table/row, `alembic upgrade head` exited 1 with the explicit unsupported/partial-schema diagnostic. The sentinel table and row remained intact, and no `workflows` table was created. The working `agentflow.db` was not used for audit migrations.
- **Runtime/browser:** Started the API against the fresh audit DB and opened `http://localhost:3000/` in Chrome. Created the starter workflow, ran both steps, observed `SUCCEEDED` and outputs `hello agentflow` and `HELLO AGENTFLOW`, refreshed, and confirmed the workflow and run remained visible. Temporary services were stopped after the check. This is a positive-path runtime check; the preceding browser verification recorded above also exercised invalid-action rejection and a persisted runtime failure.
- **Git inspection:** `HEAD` and `origin/main` both resolve to `deaf11485f89cc2df05024443881b0dbcd7a74ea`; branch is `main`; the Phase 2 working-tree changes remain unstaged/uncommitted. `.git` and its index are owned by `billy`, the index-lock file is absent, and `pgrep -a git` found no Git process. `findmnt -T .git` reports the filesystem mounted `ro`; this is the cause of the `.git/index.lock` creation failure, not a stale lock or ownership issue.
- **Final diff hygiene:** `git diff --check` passed. `apps/web/next-env.d.ts` was regenerated by `next dev`; it was restored directly to its committed contents because Git restore cannot write to the read-only mount, and it does not appear in the final working-tree changes.

### Remaining gate and limitations

- The Phase 2 implementation acceptance criteria pass. The phase remains **BLOCKED** at repository delivery: `docs/plan.md` §8.2 requires a focused local commit at phase completion, but `.git` is mounted read-only. Do not begin Phase 3 until the repository Git metadata is writable and the phase changes can be staged, reviewed, and committed.
- Only SQLite migration behavior is supported and tested. PostgreSQL is not introduced. Migrations do not delete/reset data; make a separate backup before upgrades. Automatic downgrade is intentionally disabled.
- No authentication, background worker, retry, cancellation, or AI work was added.

### Git delivery

- Phase 1 commit remains `deaf11485f89cc2df05024443881b0dbcd7a74ea`.
- Phase 2 changes are uncommitted. No push was attempted or authorized.
- **Git blocker:** Git metadata is read-only in this session. `git restore -- apps/web/next-env.d.ts` failed with `fatal: Unable to create '.../.git/index.lock': Read-only file system`. The generated file was restored directly to its committed contents; no commit or push was attempted.
