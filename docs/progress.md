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

- Synchronous API execution can hold a request open; do not expose this initial slice as a shared production service. No auth/tenant isolation, background queue, hard timeout enforcement, retries, cancellation/recovery, Alembic migration system, or AI adapter existed at Phase 1 completion.
- Docker socket access prevented verification against PostgreSQL/Redis. SQLite is the tested local path; SQLAlchemy allows a database URL to be configured, but production compatibility has not been tested.
- Browser UI flows could not be exercised because no browser surface was available. The implementation was checked via live HTTP, API-level workflows, and production build.
- **Next:** Phase 2 should formalize workflow/persistence domain contracts and introduce schema migrations before expanding execution reliability features.

### Git delivery

- The Phase 1 session could not stage because `.git` was read-only at that time. At Phase 2 start, commit `deaf11485f89cc2df05024443881b0dbcd7a74ea` was present on `main`, and local `origin/main` pointed to the same commit. This session did not push.
- Push status: not pushed by this Phase 2 session; user instruction prohibits automatic push.

## Phase 2 — Persistence Architecture and Database Migrations

**Status:** COMPLETE (implementation and acceptance checks pass; Phase 2 changes are present in the locally observed commit `e0dad172202b7a431a8845f170d9c7eea63a5cc0`)

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

- The Phase 2 implementation acceptance criteria passed. At the time of the Phase 2 audit, repository delivery was blocked by the read-only `.git` mount. During the later Phase 3 audit, commit `e0dad172202b7a431a8845f170d9c7eea63a5cc0` appeared as a child of the Phase 1 commit and contains the Phase 2 persistence changes. This session did not create that commit or contact the remote; see the Git delivery addendum below.
- Only SQLite migration behavior is supported and tested. PostgreSQL is not introduced. Migrations do not delete/reset data; make a separate backup before upgrades. Automatic downgrade is intentionally disabled.
- No authentication, background worker, retry, cancellation, or AI work was added.

### Git delivery

- Phase 1 commit `deaf11485f89cc2df05024443881b0dbcd7a74ea` remains an ancestor. The Phase 2 audit initially observed this commit at `HEAD`; the later Phase 3 audit observed `e0dad172202b7a431a8845f170d9c7eea63a5cc0` at both `HEAD` and the local `origin/main` tracking ref. That later commit contains the Phase 2 persistence changes.
- The Phase 2 audit did not create a commit or push. This Phase 3 session also did not create the newly observed commit, fetch, or contact the remote. The local `origin/main` ref is not evidence that GitHub was queried or updated.
- **Git blocker at the Phase 2 audit:** Git metadata was read-only. `git restore -- apps/web/next-env.d.ts` failed with `fatal: Unable to create '.../.git/index.lock': Read-only file system`. The generated file was restored directly to its committed contents.

## Phase 3 — Action Registry and Deterministic Execution Engine

**Status:** COMPLETE (implementation and verification pass; locally committed as `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa`)

### Scope and implementation

- Followed Phase 3 in `docs/plan.md`: explicit action contracts and metadata; deterministic execution, eligibility, state transitions and summaries; bounded safe actions; persisted outcomes; and regression, failure, repeated-run and concurrency checks. The owner explicitly authorized this local Phase 3 work while the Phase 2 Git-delivery blocker remained open.
- Replaced tuple registry entries with `ActionSpec` declarations for input/output schemas, required permissions, and timeout policy. The `/api/actions` catalog exposes these fields. Built-in actions require no permissions; permission checks fail closed for actions that declare a requirement.
- Added bounded output validation and classified safe failures: `unknown_action`, `invalid_input`, `invalid_output`, `permission_denied`, and `execution_failed`. Raw handler exceptions are not included in API errors.
- Added nullable run/step `error_category` fields with database check constraints in Alembic revision `d9156bc2a041` (`70b823ceda15` parent). Historical rows retain a null category rather than receiving a guessed classification.
- Kept the synchronous coordinator and Phase 2 transaction boundary. Workflow definitions still permit at most 50 ordered steps with backward-only references; cycles/self/forward references are rejected. Added a 512 KiB total definition bound. Run responses now include derived counts for total, succeeded, failed, and not-started steps; the dashboard displays them and exposes failure categories.
- Timeout policy is explicit but not a hard wall-clock interrupt: current handlers are bounded local deterministic operations with bounded inputs/step counts and no external I/O. Phase 1 did not record a numeric duration limit; hard timeouts, cancellation, retries, and worker recovery remain Phase 4 scope. No worker or external action was introduced.

### Verification evidence

- **Passed:** `.venv/bin/python -m pytest -q` — 25 passed. Coverage includes fresh/Phase 1/Phase 2 migration paths, repeat upgrades, preservation, integrity, transactional rollback, registry input/output/permission/error contracts, payload bounds, workflow success/failure summaries, repeated runs and concurrent run requests.
- **Passed:** `.venv/bin/ruff check agentflow tests migrations` and `.venv/bin/ruff format --check agentflow tests migrations`.
- **Passed:** `npm run lint`, `npm run typecheck`, `npm run format:check`, and standalone `npm run build`; the build prerendered `/` and `/_not-found`. The fully chained lint/typecheck/format/build command twice failed at Next.js `--showConfig` output parsing although the individual checks and standalone build passed. The cause was not established; this sequencing failure remains noted.
- **Passed:** `alembic check` on a fresh Phase 3 database reported no new operations. Fresh database migration reached `d9156bc2a041`.
- **Passed:** Actual copies of the saved Phase 1 backup and a populated Phase 2 audit database upgraded twice through `d9156bc2a041`. All prior workflow/version/run/step fields and results compared equal; historical categories remained null; foreign-key checks were clean. Neither source DB was modified.
- **Passed (Chrome):** On `/`, created and ran a two-step successful workflow; the UI showed `2 succeeded · 0 failed · 0 not started`. Then created a workflow that passed integer output into `uppercase`; the UI showed a failed run, `1 succeeded · 1 failed · 1 not started`, `invalid_input`, and the safe step error. No browser console inspection capability was available.
- **Passed:** `git diff --check`.

### Remaining gate and limitations

- Functional Phase 3 requirements and verification pass. The required local Phase 3 commit is now present. The read-only Git mount remains a blocker for further Git writes, but it does not prevent this already committed Phase 3 handoff. Do not claim remote delivery: no push was performed or authorized.
- The timeout policy does not terminate a running handler. All shipped handlers are local/pure and have bounded input and workflow sizes. No retries, cancellation, worker, or external side effects are implemented.
- The Phase 2 implementation is present in the observed `HEAD` commit. Only Phase 3 changes remain in the working tree and need review/commit when Git metadata is writable.

### Git delivery

- Branch `main`; at final audit, `HEAD` and the local `origin/main` tracking ref both resolve to `e0dad172202b7a431a8845f170d9c7eea63a5cc0`, whose parent is the Phase 1 commit. This commit contains Phase 2 changes; this session did not create it, fetch, or contact the remote.
- Phase 3 was committed locally as `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa`; the worktree was clean at the start of the Phase 4 task. `.git` remains mounted `ro`; no `.git/index.lock` or active Git process was found. No push was attempted or authorized.

### Final audit and delivery readiness — 2026-10-19

- **Diff review:** Reviewed all staged, unstaged, and untracked state. There are no staged files. The modified files and two untracked files are limited to the Phase 3 implementation, its tests, migration, README, and progress tracker. No environment file, database, cache, build output, or credential-bearing file is part of the diff. `apps/web/next-env.d.ts` has no diff. The existing `/tmp/agentflow-phase2-pre-migration.db` backup is present and was not modified.
- **Migration review:** `d9156bc2a041` declares `70b823ceda15` as its parent and adds only nullable error-category columns plus checks. Historical rows retain null categories. The prior Phase 2 revision remains unchanged. Fresh and populated-copy migration results were recorded above; migrations were not run against the working database during this audit.
- **Documentation fix:** README's current-revision statement incorrectly stopped at `70b823ceda15`; it now identifies `d9156bc2a041` as current and lists both parent revisions.
- **Frontend command issue:** The reported combined command was `npm run lint && npm run typecheck && npm run format:check && npm run build` from `apps/web`. The recorded failure was `Could not parse output from TypeScript's --showConfig.` Separate lint, typecheck, formatting, and a standalone build were recorded passing, but this audit did not rerun them. Since the error mentions Next.js parsing TypeScript configuration and the separately invoked checks do not use that Next.js build path, `npm run build` is the likely failing subcommand; the prior command output does not conclusively establish the failing command. Root cause (wrapper, tool/output parsing, or application configuration) remains unresolved. Do not label it a proven tooling-only issue.
- **Git diagnosis:** `findmnt` reports `.git` on `/dev/nvme0n1p8[/Projects/AgentFlow/.git]`, ext4 with `ro,nosuid,nodev,relatime`. `.git` and `.git/index` are owned by `billy` and have owner write bits; no index lock or active Git process exists. The mount itself prevents writes, so permissions were not changed and no Git write was retried. A read-only `git ls-remote origin refs/heads/main` attempt failed with `Could not resolve host: github.com`; remote branch state is unknown. No commit or push was attempted.
- **Checks run during this audit:** Inspected status, branch, recent commits, complete tracked diffs, untracked migration/test files, migration ancestry, README/progress, backup presence, Next.js generated-file diff, mount/ownership/lock/process state, and remote reachability. `git diff --check` passed after the README correction. Automated tests, frontend checks, migrations, and browser flows were not rerun because the implementation was unchanged in this audit; their prior results are recorded in the verification section above.
- **Acceptance and status at the time of that audit:** Deterministic execution, registered-action/input validation, dependency and transition enforcement, persisted outcomes, rejection paths, migration safety, and browser behavior had prior passing evidence. The required local commit was then absent and Phase 3 was recorded as blocked. The subsequent handoff inspection found that commit `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa` now contains the Phase 3 changes and the worktree is clean; Phase 3 is now **COMPLETE locally**.
- **Recovery at the time of that audit:** A writable copy was recommended while `.git` was read-only. No copy or Git write was performed in that audit. No push has been authorized or verified.

## Phase 4 — Background Execution, Reliability, and Recovery

**Status:** BLOCKED — implementation and Phase 4 runtime verification are complete. A local commit exists in the history-preserving writable recovery copy, but the original checkout still has uncommitted Phase 4 changes because its `.git` mount is read-only. Transfer/adopt the committed copy before treating the original project checkout as complete.

### Handoff and implementation checklist

- **Phase 3 handoff — verified complete locally:** `HEAD` is `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa`; the worktree was clean; the commit includes the Phase 3 registry, execution, migration, frontend, tests, README, and progress changes. The local `origin/main` tracking ref matches. Remote state has not been queried; no push was authorized.
- **Design decision:** No queue or worker dependency exists in the inspected manifests. Use a durable SQLAlchemy-backed queue in the existing database, with run creation and queue insertion committed atomically. Run a separate worker process so API instances do not execute work in their request handlers. This keeps SQLite as the documented local path and avoids assuming Redis/Celery infrastructure. Add lease-based claiming and recovery; database unavailability must fail enqueue without returning success.
- **Acceptance 1 — timeout/retry policy — passed:** Configurable per-action hard timeout and bounded retry/backoff; attempts and outcomes are persisted and exposed. Backend tests passed. In Chrome, a test-only worker override of `ACTION_TIMEOUTS={"set_value":0.001}` induced three action timeouts; run #4 became `FAILED` with `retries_exhausted` and visible attempts `#1 timed_out`, `#2 timed_out`, `#3 timed_out`. The override was only on the worker for fault injection; normal deployments must configure API and worker consistently.
- **Acceptance 2 — duplicate delivery — passed for documented guarantees:** One durable job per run, fenced leases, optional idempotency-key deduplication, and safe behavior for current pure actions. Targeted API test passed (1 passed); a separate stale-worker check confirmed generation 1 was fenced after generation 2 reclaimed the lease. No exactly-once external-side-effect claim.
- **Acceptance 3 — interruption recovery — passed:** Expiring leases resume from completed steps and record interrupted attempts. In the disposable browser DB, run #5 was placed at a persisted running-attempt boundary, its lease expired, and the live worker reclaimed it; Chrome showed `SUCCEEDED` with `#1 interrupted (worker_interrupted)` then `#2 succeeded`. This injected the recoverable crash state; it did not kill a process during an external side effect. Current actions are pure local operations; external side-effect actions remain unsupported without a reconciliation contract.
- **Acceptance 4 — cancellation — passed:** Explicit queued/running transitions, cancellation endpoint, bounded action termination, and visible terminal state. In Chrome, queued runs #1 and #2 were cancelled before claim; both showed `CANCELLED`, zero completed steps, and the cancellation message. Active cancellation is covered by backend worker tests.
- **Acceptance 5 — queue/worker unavailability — passed:** Enqueue commits run/job before returning accepted; a missing worker left run #2 queued until cancellation; database failures do not report success (automated coverage).
- **Migration/API/UI — implemented and checked:** New migration, additive API/cancel/attempt fields, dashboard polling/status/cancel, and worker/recovery documentation. Migration and frontend checks passed; see verification results below.
- **Potential regressions:** Existing API tests currently expect synchronous terminal run responses; update those contracts deliberately while preserving workflow creation, validation, versioned runs, action errors, and history. SQLite locking/concurrency needs specific verification.

### Phase 4 implementation and verification — 2026-10-19

- **Implemented:** Durable database-backed run queue with atomic run/job creation; a separate worker with expiring fenced leases, retries/backoff, hard per-action process timeouts, cancellation, graceful shutdown, and recovery from completed steps. Current actions are pure local operations. The implementation does not claim exactly-once external effects; future side-effect actions require an idempotency/reconciliation contract.
- **Persistence/API/UI:** Added migration `6ab9d731c2f0` for queue jobs, step attempts, new run/step statuses, cancellation metadata, and idempotency keys. Run endpoints enqueue and return HTTP 202, support the optional `Idempotency-Key`, and expose attempt history; added `POST /api/runs/{id}/cancel`. Dashboard polls active runs, displays attempts, and supports cancellation.
- **Configuration and operation:** Added validated `ACTION_TIMEOUTS`, worker command `python -m agentflow.worker` / `make worker`, and setup/recovery documentation in README. API and worker must share the same database and action timeout configuration.
- **Architecture decision:** No queue dependency was present. A SQLAlchemy queue in the existing database provides the transactional outbox equivalent without adding Redis/Celery or changing the local SQLite path.
- **Backend verification:** `.venv/bin/python -m pytest -q` — **35 passed in 31.65s**. Worker tests exercise retry and timeout policy, cancellation, duplicate claims, lease expiry/recovery, and persisted attempts. API regressions and migration/persistence tests are included.
- **Static/frontend verification:** `.venv/bin/ruff format agentflow tests migrations` and `.venv/bin/ruff check agentflow tests migrations` passed. In `apps/web`, `npm run lint`, `npm run typecheck`, `npm run format:check`, and `npm run build` passed. The generated `apps/web/next-env.d.ts` change from the build was restored; it is absent from the final diff.
- **Migration verification:** Fresh disposable SQLite DB upgraded to `6ab9d731c2f0`; `alembic check` reported no new upgrade operations. Earlier implementation checks covered populated copies and repeated upgrades; the existing working DB was not modified during Phase 4.
- **Browser verification:** Chrome route `http://localhost:3000/`; dashboard showed `API connected`. The API/frontend were already serving the disposable DB `/tmp/agentflow-phase4-browser.db` at revision `6ab9d731c2f0`; the separate documented worker command was started with that exact `DATABASE_URL`. Run #6 was explicitly observed `QUEUED`, then the worker was started with `python -m agentflow.worker --once`; the same run reached `SUCCEEDED` with 2/2 steps and outputs `hello agentflow` and `HELLO AGENTFLOW`. Refreshed the dashboard and confirmed the terminal state and outputs remained. Run #4 exercised three bounded hard timeouts and showed terminal retry exhaustion plus full attempt history; refresh retained it. Run #5 recovered a persisted running attempt after lease expiry and showed `SUCCEEDED` with interrupted/replayed attempts; refresh retained it. Runs #1/#2 were cancelled while queued and remained visibly cancelled. Read-only SQLite inspection confirmed persisted run, job, output, attempt, and lease-generation values for runs #1–#6. Browser console warning/error log query returned no entries; a separate direct navigation to port 8000 was blocked by the Chrome client, so the dashboard itself was used for API behavior.
- **Idempotency/fencing verification:** `tests/test_workflows.py::test_idempotency_key_returns_same_run_and_creates_one_job` rerun — **1 passed in 0.97s**. A separate disposable SQLite scenario claimed a job as generation 1, expired/reclaimed it as generation 2, confirmed the stale claim could not create a step or change the queued run, then confirmed the current claim completed successfully (`stale_generation=1 fenced=true winning_generation=2 terminal=succeeded`).
- **Diff/integrity checks:** `git diff --check` passed. Final worktree contains only Phase 4 source, migration, tests, frontend, README, and progress changes; no database, credential, or generated build artifact is included. The Chrome check created one `Welcome workflow` and two cancelled runs in its connected local service.
- **Git delivery blocker:** Original branch is `main`; original `HEAD` and local `origin/main` tracking ref are `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa`; remote state was not queried. `.git` is mounted `ro,nosuid,nodev,relatime` although the directory/index owner bits include write; no index lock exists. This mount prevents index/ref writes in the original checkout. A writable history-preserving copy at `/tmp/AgentFlow-phase4-closure-copy` was created using `git clone --no-hardlinks --no-local`; only Phase 4 changes were overlaid and reviewed. Local implementation commit `a15d1046afcb26d47e3481faacfd149746407168` was created on the copy's `main`, parent `a835ee9b48e04aa0401370f06ac5e6ba239a2bfa`. The original checkout remains uncommitted. No push was attempted or authorized.
- **Acceptance status:** All functional Phase 4 criteria and browser checks now pass. The required local commit is still blocked in the authoritative checkout, so Phase 4 is **not declared complete** under the project gate. No Phase 5 work was started.
- **Next action:** Adopt `/tmp/AgentFlow-phase4-closure-copy` as the working checkout or, after restoring writable Git metadata, transfer commit `a15d1046afcb26d47e3481faacfd149746407168` onto the original `main`. Verify the intended commit in the active project checkout before declaring Phase 4 complete. Push only if separately authorized.
