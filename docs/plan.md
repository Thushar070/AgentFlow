# AgentFlow — Project Implementation Plan

> **Repository:** [Thushar070/AgentFlow](https://github.com/Thushar070/AgentFlow)  
> **Document status:** Implementation baseline for an intentional fresh project. The checkout was confirmed to contain only these planning documents before initialization.
> **Important:** The original Phase 1 was discovery-only. The project owner explicitly authorized fresh-project initialization and a working deterministic vertical slice, so Phase 1 is revised below to include implementation. This authorization supersedes the earlier prohibition on Phase 1 product code.

## 1. Project Overview

### 1.1 Purpose

AgentFlow is intended to be a reliable workflow execution platform with an optional AI layer. The core product should be useful without an LLM: users and APIs should be able to define, validate, start, observe, and manage deterministic workflows. An AI layer may later translate natural-language requests into structured workflow proposals that pass through the same validation, authorization, approval, and execution controls as manually created workflows.

This statement describes the agreed product direction, not an assertion that the repository already implements any of these features. Repository inspection and clarification take precedence wherever actual project materials differ from this planning baseline.

### 1.2 Product goals

1. Deliver a useful non-AI workflow product before integrating an LLM.
2. Model workflow and step states explicitly and persist execution progress.
3. Execute only registered, schema-validated actions; do not execute arbitrary model-generated code, SQL, or shell commands.
4. Provide defensible failure-handling behavior for timeouts, retries, cancellation, worker interruption, and uncertain external side effects.
5. Provide authentication and authorization appropriate to the confirmed product scope, and enforce access checks on the server.
6. Make workflow runs understandable through run history, step results, structured errors, and audit events.
7. Add an optional local LLM integration through Ollama only after the core product is verified. Keep model-provider integration behind a replaceable interface if this is compatible with the verified codebase and approved scope.
8. Verify each implementation phase with automated tests and browser-based user testing where applicable, and commit/push only after the phase passes its completion gates.

### 1.3 Intended outcomes

The completed project should provide:

- A working workflow product that does not require an LLM to create or execute valid workflows.
- A documented workflow definition format and state-transition model.
- A bounded action registry with typed input/output contracts.
- Persistent workflow/run/step records and a traceable execution history.
- Documented retry, timeout, cancellation, recovery, and idempotency behavior.
- Appropriate security boundaries and auditability.
- A browser-accessible interface for the features confirmed in scope.
- An optional AI proposal flow that cannot bypass deterministic validation or authorization.
- Reproducible setup, test, and verification instructions.
- Truthful progress and test evidence for every phase.

### 1.4 Out of scope unless explicitly approved

Do not silently add these or treat them as initial requirements:

- Microservices, distributed orchestration across multiple regions, or Kubernetes.
- Arbitrary code execution, autonomous shell access, or unrestricted SQL generation by the LLM.
- Integrations with real payment, refund, email, inventory, or order-management providers before their requirements and credentials are approved.
- A public multi-tenant SaaS launch, billing, subscription plans, or enterprise SSO.
- RAG, vector databases, fine-tuning, or model training unrelated to the agreed workflow-agent purpose.
- Claims of exactly-once execution for arbitrary external side effects.
- Unrequested redesigns, dependencies, frameworks, or broad refactors.

If the repository already includes any of these, inspect and document the existing facts, then clarify whether they are to be retained before changing them.

## 2. Repository Inspection and Source of Truth

### 2.1 Target

- Repository URL: `https://github.com/Thushar070/AgentFlow.git`
- Expected local project root: repository root directory, referred to as `/AgentFlow/` in these instructions.
- Required instruction files: `/AgentFlow/docs/plan.md` and `/AgentFlow/docs/agents.md`. Both files must remain inside the repository's `docs/` directory.

The target is described as private. The preparing session received `404 Not Found` when attempting to inspect it, so repository contents remain unverified. This plan must not be read as evidence that the target repository was successfully inspected.

### 2.2 Mandatory initial inventory

Before implementation, the agent must:

1. Confirm the working directory and repository root.
2. Run and record `git status --short --branch`, `git remote -v`, and `git log -5 --oneline` where Git is available.
3. Inspect all root-level files and relevant subdirectories, including README files, manifests, lockfiles, environment examples, Docker configuration, migrations, tests, CI workflows, and existing source code.
4. Identify existing application entry points, runtime versions, frameworks, package managers, lint/type-check tools, database access patterns, and testing commands from actual files.
5. Identify current branch, default branch if available, repository remote, working-tree modifications, and whether the agent has permission to read, commit, and push.
6. Read any existing project documentation and tests before proposing modifications.
7. Never expose secret values while inspecting environment files. Record variable names and whether configuration is documented, not credentials.
8. Create a short baseline inventory in the project's progress log using observed facts, unknowns, and assumptions as separate lists.

### 2.3 Decision rules

- Existing working implementation and repository conventions take precedence over the proposed structure below unless they conflict with a confirmed product requirement or a security/correctness requirement.
- A package being installed does not prove it is used; verify imports, configuration, or tests.
- A README claim does not prove a feature works; run the relevant application and tests.
- Do not replace the current stack solely because this plan lists an example or common option.
- If the repository is inaccessible or a material decision cannot reasonably be made from the owner-approved scope, ask for clarification. An intentionally empty fresh checkout is not itself a blocker when the owner has authorized initialization.

## 3. Technology Stack

### 3.1 Confirmed versus proposed

No existing languages, frameworks, dependencies, services, or versions could be verified against the private repository during preparation. The following is a **proposed baseline based on the product discussion**, not a statement about the current implementation. Verify it against the repository and obtain clarification before introducing conflicting technology or substantial dependencies.

| Area | Proposed choice | Intended responsibility | Verification / decision rule |
|---|---|---|---|
| Frontend | Next.js + TypeScript | Workflow management, execution views, logs, and approvals | Retain an existing frontend if present; confirm framework/version from manifests. |
| API/backend | Python + FastAPI | HTTP API, validation, orchestration boundary | Confirm Python version and existing server framework before adopting. |
| Data validation | Pydantic or the existing equivalent | Request and workflow schema validation | Avoid duplicating validation frameworks without a clear need. |
| Primary database | PostgreSQL | Durable definitions, runs, step results, approvals, and audit records | Verify existing persistence layer and migrations first. |
| Database access | SQLAlchemy + Alembic, or existing ORM/migration tool | Persistence and schema changes | Follow repository conventions if a working alternative already exists. |
| Background jobs | Redis + Celery or an approved equivalent | Queueing and background worker coordination | Do not introduce until asynchronous execution requirements are confirmed. PostgreSQL remains the durable source of truth for execution state. |
| Local LLM | Ollama, later phase only | Optional natural-language workflow proposal generation | Do not require Ollama for core startup, tests, or non-AI workflows. |
| Frontend styling/components | Existing project choice; otherwise select only after inspection | Consistent UI | Do not add a UI framework without inspecting current dependencies and clarifying material choices. |
| Testing | Existing tools; likely Pytest for Python and the chosen frontend test tools | Unit, integration, browser, and regression tests | Derive actual test commands from manifests and repository CI. |
| Browser automation | Existing supported tooling; otherwise clarify/select the smallest suitable option | Real-user browser checks | Verify tool availability. Never claim browser tests without executing them. |
| Containers | Docker / Docker Compose if compatible with repository | Repeatable local services and development | Inspect existing container configuration before creating or replacing it. |
| Git hosting | Private GitHub repository above | Version control and phase delivery | Verify remote, permissions, branch, and credentials before push. |

### 3.2 Dependency principles

- Prefer existing dependencies and standard library facilities where suitable.
- Pin or constrain versions according to the repository's existing dependency-management conventions.
- Do not introduce a new orchestration or agent framework that obscures the execution engine's behavior unless explicitly justified and approved.
- Keep provider-specific LLM code behind a narrow adapter/interface where practical.
- Do not commit API keys, passwords, private URLs containing credentials, `.env` files, or production data.
- Update lockfiles when dependencies change and verify clean installation from the documented manifests.

## 4. Functional Requirements and Key Design Constraints

These requirements are the intended product baseline. If a requirement materially conflicts with existing project code or needs an unresolved business decision, stop and ask rather than invent behavior.

### 4.1 Core product (no AI dependency)

- Create, read, update, validate, and manage workflow definitions within authorized access boundaries.
- Represent each workflow using a documented, versioned structure with steps, registered action identifiers, inputs, and dependency/ordering information appropriate to the approved initial scope.
- Start a workflow run and durably record its state.
- Execute only registered actions and validate action inputs against declared schemas.
- Persist step status, attempts, outputs or safely redacted output references, errors, timestamps, and audit events as appropriate.
- Expose run status and an inspectable history through the API and UI where applicable.
- Support controlled failure handling, cancellation, and recovery behavior defined during design.
- Enforce authorization server-side for every protected read and mutation.

### 4.2 Execution correctness

- Define allowed workflow and step state transitions explicitly.
- Guard concurrent state changes using database constraints, transactions, locking, optimistic concurrency, or a verified equivalent where necessary.
- Separate durable workflow state from transient queue state. Queue acknowledgement alone is not proof that a workflow step completed.
- Treat retries as at-least-once attempts unless the individual action contract proves stronger behavior. Use idempotency keys, deduplication, transactional handling, or reconciliation where applicable.
- Do not promise exactly-once effects across arbitrary external systems.
- Do not retry every error indiscriminately. Classify retryable and non-retryable failures; make retry limits and timing explicit.
- Define how a worker interrupted between performing a side effect and persisting its result is reconciled.
- Protect against endless loops, runaway execution, unbounded inputs, and excessive step counts with validated limits.

### 4.3 Security and human approval

- Treat all user input, persisted workflow payloads, action results, and model outputs as untrusted.
- Validate action identifiers and arguments against an allowlist and schema before execution.
- Enforce user/tenant ownership and action permissions on the server, not only in the UI.
- Do not execute model-generated source code, shell commands, or raw SQL.
- Require approval for configured sensitive operations. A pending approval must not be treated as permission to execute.
- Record approval identity, decision, and timing as allowed by the data model; prevent stale or unauthorized approvals.
- Redact secrets and sensitive data from logs, error messages, traces, and model context.
- Use safe defaults for CORS, cookie/session/token handling, rate limits, and security headers consistent with the existing authentication design.
- Include tests for privilege escalation, cross-user access, malformed inputs, prompt injection through untrusted content where relevant, and attempts to invoke disallowed tools.

### 4.4 Optional AI layer

- The AI layer must be feature-isolated and optional.
- Natural-language input may produce a structured **proposal**, not an unrestricted executable program.
- Validate model output syntactically and semantically, including registered actions, argument schemas, step/dependency limits, and policy constraints.
- Present or otherwise explicitly approve the proposal according to the agreed risk policy before execution, especially for state-changing or sensitive actions.
- Pass approved proposals through the same core workflow engine used by manually authored definitions.
- Handle model timeout, unavailability, malformed output, refusal, and tool-selection errors without corrupting or bypassing core product state.
- Keep the core non-AI test suite runnable when Ollama or a model is unavailable. AI tests should be isolated and marked according to the project's test conventions.
- Do not assume a particular model supports all desired tool-calling or structured-output behavior. Verify the selected model and record limitations during the AI phase.

## 5. Proposed System Architecture

> **Proposed logical design, subject to repository inspection.** Adapt module names and placement to the actual codebase. Do not create a monorepo or move existing files merely to match this example.

### 5.1 Components and responsibilities

1. **Web application:** Presents workflow forms/editor, validation feedback, run controls, run history, step-level traces, approvals, and settings supported by implemented backend endpoints.
2. **API boundary:** Authenticates requests, authorizes resource access, validates payloads, exposes stable contracts, and records domain commands.
3. **Workflow domain/service:** Owns workflow definitions, versions, dependency validation, lifecycle rules, and permitted transitions.
4. **Execution coordinator:** Creates runs and steps transactionally, decides eligible work, enforces execution limits, and coordinates cancellation/recovery.
5. **Action registry:** Provides a bounded catalog of registered actions and their schemas, permission declarations, and execution handlers.
6. **Action runtime:** Validates action inputs, enforces execution context/permissions, invokes handlers, classifies outcomes, and records results.
7. **Worker/queue adapter:** Runs eligible steps asynchronously where required, coordinates retry timing, acknowledges jobs correctly, and relies on persistent state rather than queue messages as the source of truth.
8. **Persistence layer:** Stores definitions, versions, runs, step attempts, approvals, idempotency records, and audit events with appropriate transactions and constraints.
9. **Observability/audit:** Provides structured logs and inspectable events while redacting sensitive information.
10. **AI adapter (later):** Converts user requests into proposed, schema-constrained workflow definitions. It has no privileged bypass into the database or action runtime.

### 5.2 Data flow

1. A user creates a deterministic workflow or submits a natural-language request.
2. For deterministic workflows, the API validates the definition. For AI requests, the AI adapter returns a proposal that is independently validated.
3. The server authenticates the user and checks resource/action permissions. The workflow is stored or a proposed definition is returned for review as appropriate.
4. On execution request, the server creates a durable workflow run and the initial step records in a transaction.
5. Work is dispatched to the worker through the verified queue mechanism. Where needed, use a transactional outbox or equivalent to avoid a database-commit/queue-publish gap.
6. The worker claims an eligible step, verifies its current state, checks policy/approval, validates action arguments, and invokes the registered action.
7. The outcome and state transition are persisted. Eligible steps proceed; failures follow documented retry/recovery policy; approvals pause sensitive steps.
8. The UI obtains the persisted status and execution trace through authorized read APIs.

### 5.3 Dependencies and boundaries

- The web app depends on the API, not direct database access.
- The API and worker share domain/service contracts and persistence rules; avoid duplicating state-transition logic.
- The queue is a delivery/coordination component, not the authoritative record of completion.
- The action registry must not expose arbitrary function imports or dynamic code execution from user/model input.
- The AI adapter depends on the proposal schema and approved action catalog, while the core domain remains AI-agnostic.
- External services, if added, must be behind explicit adapters with documented timeouts, error classification, secrets management, and test doubles.

### 5.4 Candidate directory structure

This is a reference, not a mandatory refactor. Preserve an existing, coherent structure.

```text
AgentFlow/
├── README.md
├── .env.example
├── docker-compose.yml                 # only if confirmed/used
├── apps/
│   ├── api/                           # backend entry point, if split by app
│   ├── worker/                        # background worker entry point, if separate
│   └── web/                           # frontend, if repository uses this layout
├── backend/                           # alternative single-backend layout
│   ├── auth/
│   ├── workflows/
│   ├── execution/
│   ├── actions/
│   ├── policies/
│   ├── approvals/
│   ├── recovery/
│   ├── audit/
│   ├── database/
│   └── ai/                             # introduced only in the AI phase
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── recovery/
│   └── security/
└── docs/
    ├── plan.md                        # mandatory implementation plan
    ├── agents.md                      # mandatory AI-agent instructions
    └── progress.md                    # or the existing progress-tracking location
```

## 6. Implementation Phases

The following phase sequence reflects the fresh-project authorization. Phase 1 establishes the first working product slice. Later phases retain the reliability, security, expanded dashboard, and AI work; do not treat the initial synchronous engine as production-ready background execution.

### Phase 1 — Fresh Project Foundation and Deterministic Workflow Slice

**Objective:** Establish a reproducible web/API foundation and deliver a working non-AI workflow from definition through persisted execution results.

**Dependencies:** Repository access and read access to the current checkout.

**Tasks:**
- Record the verified fresh-checkout state, available runtimes, services, and selected stack in the progress tracker.
- Establish Python/FastAPI/Pydantic/SQLAlchemy and Next.js/TypeScript boundaries with documented setup commands and example configuration.
- Implement health and action-catalog endpoints plus versioned workflow validation and bounded input/step limits.
- Implement a controlled action registry and sequential synchronous execution with persisted workflow, run, and step outcomes.
- Expose create/list/inspect/run APIs and a usable dashboard that communicates with the API.
- Add tests for valid definitions, invalid definitions, successful execution, and failed steps.
- Document the synchronous execution limitation and defer background queues, authentication, and migrations to later phases.

**Deliverables:** Reproducible app foundation; minimal working workflow product; automated tests; setup and architecture documentation; progress evidence.

**Acceptance criteria:**
- The API starts and responds to health checks; the frontend builds and loads and reaches the API.
- Valid workflow definitions persist, can be inspected, and execute registered steps with persisted results.
- Invalid definitions and unknown actions are rejected; step failures are recorded and stop subsequent work.
- Relevant automated tests, linting, formatting, type checking, and builds pass, or limitations are explicitly recorded.
- Runtime and browser behavior are tested when the available environment permits; unavailable services/tools are documented accurately.
- The implementation and its limitations are documented, and no later-phase feature is represented as complete.

**Verification:** Run backend tests and quality checks, frontend lint/type/build checks, start both apps, exercise valid and invalid API workflows, and verify the dashboard in a real browser when available. For this owner-authorized fresh-project initialization, if browser tooling is unavailable, record the limitation and use the strongest available runtime alternative; do not claim browser verification.

### Phase 2 — Core Domain, Persistence, and Workflow Management

**Objective:** Build or complete the non-AI workflow definition and storage layer.

**Dependencies:** Phase 1 accepted contracts and decisions.

**Tasks:**
- Implement domain models and migrations using the verified stack.
- Implement workflow create/read/update/list/archive or delete behavior as approved, including ownership checks.
- Implement workflow-definition schema validation and versioning rules.
- Enforce allowed transitions and data-integrity constraints.
- Implement API contracts and request/response error behavior.
- Add authentication/authorization integration following the verified security design.
- Create seed/demo data only when suitable, clearly separating development fixtures from production data.

**Deliverables:** Persisted workflow definitions; migration scripts; validated API endpoints; unit/integration tests; updated setup/API documentation.

**Acceptance criteria:**
- Valid definitions persist and can be retrieved; invalid or oversized definitions are rejected with safe, useful errors.
- Unauthorized users cannot read or modify protected workflow resources.
- Updates/versioning do not silently corrupt already-created runs or published definitions.
- Migrations work on a clean database and the supported upgrade path.
- No AI dependency is required for core workflow management.

**Verification:** Unit tests for schemas and domain rules; integration tests for persistence, migrations, auth, concurrent updates, not-found and malformed requests; regression tests for existing behavior; browser testing for applicable workflow-management pages.

### Phase 3 — Action Registry and Deterministic Execution Engine

**Objective:** Execute registered actions through a deterministic, AI-independent workflow engine.

**Dependencies:** Phase 2 stable models and contracts.

**Tasks:**
- Define action registration, input/output schemas, permission metadata, timeout policies, and error categories.
- Implement workflow run creation, step creation, eligibility/dependency checks, execution-state transitions, result persistence, and run summaries.
- Create a small, safe set of deterministic example actions appropriate to the approved domain; use mocks instead of unapproved real external systems.
- Enforce step, payload, duration, and execution limits as decided in Phase 1.
- Ensure the worker and API use shared domain services for transitions.
- Persist action outcomes and traceable events without storing secrets.

**Deliverables:** Action registry; deterministic engine; initial actions; state model; run/step APIs; automated tests and developer documentation.

**Acceptance criteria:**
- A valid workflow runs end-to-end without any LLM process or model installed.
- Only registered actions with valid arguments can execute.
- Dependencies and permitted state transitions are enforced.
- Step and workflow outcomes persist, and run history can explain each result.
- Invalid action names, invalid arguments, dependency cycles or unsupported states are rejected.

**Verification:** Unit tests for registry, validators, transitions, dependencies and error classifications; integration tests for successful and failed runs; edge/negative tests for malformed definitions, missing actions, dependency cycles, repeated start requests and concurrent state updates; browser testing of create/start/view flows when UI exists.

### Phase 4 — Background Execution, Reliability, and Recovery

**Objective:** Make execution resilient to transient errors and worker interruption without making unsupported exactly-once claims.

**Dependencies:** Phase 3 deterministic execution behavior.

**Tasks:**
- Implement or integrate the approved job queue/worker mechanism, only after repository and design verification.
- Ensure durable workflow/step records are authoritative and queue dispatch does not lose committed work; implement transactional outbox or an approved equivalent if needed.
- Add configurable per-action timeouts, bounded retries, backoff, cancellation behavior, and explicit retryable/non-retryable failure categories.
- Implement idempotency/deduplication for supported operations and define reconciliation for uncertain external side effects.
- Handle worker restart, duplicate deliveries, stuck runs, expired leases/claims, and graceful shutdown as relevant to the chosen design.
- Define recovery behavior in documentation and expose meaningful status/events.

**Deliverables:** Worker/queue integration; recovery policy; failure injection tests; operational notes; updated runbook.

**Acceptance criteria:**
- Retry limits and timeout policies are enforced and observable.
- Duplicate job delivery does not create invalid duplicate state transitions; action side effects are protected only to the extent supported by their idempotency contract.
- Interrupted work can resume or be reconciled according to documented rules.
- Cancellation and terminal-state behavior are consistent and tested.
- Queue or worker unavailability does not falsely mark steps as successful.

**Verification:** Unit/integration tests for timeout, backoff, duplicate delivery, worker crash at critical boundaries, restart/replay, queue outage, database outage, cancellation races, concurrent workers and uncertain side effects; regression tests; browser-based inspection of status and recovery controls where applicable. Any environment limitation must be logged as a blocker, not silently skipped.

### Phase 5 — Security, Approvals, Auditability, and Observability

**Objective:** Add the security and operational controls required for a dependable workflow product.

**Dependencies:** Stable execution and recovery model from Phases 2–4.

**Tasks:**
- Complete the approved authentication and server-side authorization model.
- Implement approval states and policies for configured sensitive actions, including approver permissions and stale/duplicate approval handling.
- Implement structured, inspectable execution events and audit records.
- Redact secrets and sensitive fields from logs, errors, traces, and persisted outputs where appropriate.
- Add input size/depth limits, execution quotas or rate limits appropriate to the scope, and safe error handling.
- Review object/resource-level access boundaries, workflow tampering, privilege escalation, replay/idempotency bypass, and action-registry misuse.
- Add operator-visible failure detail without leaking protected data.

**Deliverables:** Approval flow; audit/event model; security controls; security test suite; updated threat model and operational guidance.

**Acceptance criteria:**
- Sensitive actions remain blocked while required approval is pending or invalid.
- Authorization is enforced server-side for every protected endpoint and action.
- Audit history accurately records meaningful state transitions and approval decisions without exposing secrets.
- Attack-oriented tests for relevant access-control and malformed-input cases pass.
- No known critical/high security defect remains unresolved; any other material risk is documented and accepted by the user before proceeding.

**Verification:** Unit/integration/security tests; negative tests for unauthorized actions, cross-user access, replay, malformed approval, prompt-like content in untrusted fields, log injection and secret leakage; regression tests; browser verification for approvals, permissions and audit pages where applicable.

### Phase 6 — User Interface, End-to-End Workflows, and Product Readiness

**Objective:** Deliver a coherent browser-based experience for all agreed non-AI product capabilities and prove core workflows work as a user would experience them.

**Dependencies:** Phases 2–5 APIs and security controls. UI work may occur earlier only if the repository already requires it, but final acceptance remains here.

**Tasks:**
- Build or complete the agreed workflow-management and run-observation screens using the verified frontend stack.
- Provide loading, empty, validation, permission-denied, offline/API-error, running, failed, cancelled, and completed states.
- Provide run detail, step history, event/error inspection, and approval interactions in scope.
- Validate frontend forms as a usability aid while relying on server-side validation for enforcement.
- Document local setup, configuration variables, migrations, start/stop commands, test commands, architecture, failure semantics, and known limitations.
- Run a full regression pass over the non-AI product.

**Deliverables:** Working web application for agreed scope; end-to-end tests; setup/operations documentation; release-readiness checklist.

**Acceptance criteria:**
- A user can complete the agreed primary workflow from the browser without relying on AI.
- Browser-visible state reflects persisted backend results and handles refresh/navigation coherently.
- Errors, permissions, approvals, and terminal states are understandable and do not expose protected data.
- Clean setup and documented startup work in the supported environment.
- Unit, integration, end-to-end, security, and regression tests required for the scope pass; unavailable tests are explicitly recorded as blockers.

**Verification:** Start all required services and application processes. Use browser automation when available; otherwise use an available browser manually and report the method. Test primary happy paths, empty state, invalid forms, failed actions, refresh, permissions, cancellation/approval, repeated clicks/submissions, service failures, and viewport/accessibility basics as appropriate. Fix root causes and rerun the affected suite.

### Phase 7 — Ollama AI Integration, Evaluation, and Final Verification

**Objective:** Add an optional AI interface that proposes workflows while preserving all non-AI controls.

**Dependencies:** Phases 1–6 accepted; core product must run without Ollama.

**Tasks:**
- Verify the Ollama installation/API and selected model's actual structured-output/tool-calling capabilities before selecting implementation details.
- Define a constrained proposal schema for natural-language request → workflow proposal.
- Add provider adapter and bounded timeouts, safe error handling, and configuration. Do not expose model service or model output directly to privileged execution.
- Validate proposed action names, arguments, dependencies, allowed effects, step/payload limits and user permissions server-side.
- Add user review/approval for generated workflows and mandatory approval rules for sensitive operations.
- Execute accepted proposals through the same deterministic workflow engine/action registry.
- Add a fixed evaluation set spanning clear requests, ambiguous requests, invalid tool names, malformed arguments, disallowed actions, prompt injection/untrusted text, model timeout/unavailability, malformed output, and multi-step requests.
- Measure and report real results such as proposal schema-validity rate, task completion, correct action selection, unauthorized-action rejection, latency, failure modes and recovery behavior. Do not prefill or fabricate metrics.
- Ensure all core tests work with AI disabled and all optional AI tests clearly report model/environment requirements.

**Deliverables:** Isolated Ollama adapter; constrained AI proposal flow; evaluation dataset and results; security notes; final architecture/setup documentation; final regression evidence.

**Acceptance criteria:**
- Core workflows continue to run with Ollama stopped or absent.
- Model output never executes directly and cannot bypass the action registry, authorization, approval policy, or workflow validator.
- Invalid or unsafe proposals are rejected safely and leave no unintended side effects.
- Model errors/timeouts are reported without corrupting workflow state.
- Evaluation results come from a repeatable test run and include failures/limitations.
- Browser-based user testing verifies request → proposal → review/approval → execution → trace on the actual application.

**Verification:** Unit, integration, security, evaluation, and regression tests; start the full application and model service where available; test with a real user-style browser journey; test AI-unavailable and malformed-output behavior. If model download, hardware, service access, or browser tooling prevents verification, stop and record the blocker instead of claiming completion.

## 7. Verification Standard for Every Phase

Every phase uses the following mandatory sequence:

1. **Pre-check:** Re-read `docs/plan.md` and `docs/agents.md`; confirm current phase, scope, dependency completion, working-tree status, and existing uncommitted changes.
2. **Implement:** Complete only the current phase's agreed tasks.
3. **Review:** Inspect the diff for scope creep, unsafe behavior, secret exposure, migrations, backward compatibility, and test gaps.
4. **Automated tests:** Run the relevant formatter, linter, type checker, unit, integration, security, end-to-end, and regression commands available and applicable to the repository. Use the actual configured commands; do not invent command names or imply a tool exists before inspection.
5. **Manual/interactive verification:** Start the relevant services and application. Use a browser and supported browser automation to follow primary and negative user journeys wherever applicable. Record browser/tool limitations explicitly.
6. **Failure handling:** For each failure, capture its reproduction and evidence, investigate the root cause, fix it where possible, and rerun targeted tests followed by the relevant full regression suite. Do not suppress, skip, weaken, or rewrite tests merely to obtain a green run.
7. **Acceptance review:** Check every phase-specific criterion. Separate verified, failed, not applicable, and blocked items; explain why anything is not applicable or blocked.
8. **Evidence update:** Update the progress log with commands run, environment, exit status, key results, browser checks, known issues, and current status.
9. **Git safety check:** Review `git diff`, `git diff --check`, staged files, secrets, generated files, and working-tree changes. Do not include unrelated work or user changes.
10. **Commit and push:** After every implementation and verification gate passes, create a focused local commit when permitted. Do not push unless the user explicitly authorizes a push in the current task. Report remote state separately.
11. **Phase gate:** Do not begin the next phase until all implementation and verification criteria are satisfied. A remote push is not authorized by default and is not required for a local phase status report.

### 7.1 Browser verification requirements

Browser testing is mandatory for phases that change or rely on browser-visible behavior. It must resemble real user operation, not just direct API calls. Verify navigation, forms, state updates, loading and error displays, permissions, run controls, approvals, and relevant responsive behavior. Use the available supported browser automation tool when possible. If no browser or automation capability is available, report that limitation, perform only the alternative checks that are actually available, and leave the browser-verification gate open until it can be satisfied or the user explicitly authorizes a different standard.

### 7.2 Test evidence rules

For each run, record:

- Command or exact manual scenario.
- Relevant environment/runtime and configuration (without secrets).
- Pass/fail/blocked status and exit code where available.
- Test counts and concise failures or logs references where useful.
- Browser route and user actions for UI checks.
- Defects found, root cause, change made, and retest outcome.

A test that was not run is not passed. A build that succeeds does not prove the feature works. A screenshot alone does not prove backend correctness. A mocked test does not prove an external integration works.

## 8. Git Workflow and Repository Safety

### 8.1 Before work

- Confirm remote is `https://github.com/Thushar070/AgentFlow.git` or its authenticated equivalent without embedded credentials.
- Confirm branch and upstream; use the repository's established branch conventions.
- Inspect uncommitted changes and never overwrite, reset, stash, or discard user work without permission.
- If repository access, credential permissions, or branch-protection requirements prevent required actions, stop and ask for assistance.

### 8.2 At each phase completion

- Ensure only changes belonging to the completed phase are staged.
- Review the entire staged diff and search for secrets, local paths, generated artifacts, debug output, and unrelated edits.
- Run `git diff --check` plus the required tests and browser verification before commit.
- Use a descriptive commit message. Suggested pattern: `Phase N: <implemented capability>`; message details must match the actual diff.
- Commit only after all phase gates pass.
- Do not push unless the user explicitly authorizes a push in the current task. Never force-push unless the user explicitly authorizes it and its necessity is established.
- For an authorized push, verify success and that the remote branch points to the intended commit. Record the commit hash and remote branch.
- If a commit or explicitly authorized push fails, do not claim success; diagnose the failure and report credentials, permissions, conflicts, or policy blockers.

### 8.3 Files and changes that must never be committed

- Secret values, access tokens, private keys, unredacted database dumps, personal data, and production records.
- Local virtual environments, dependency caches, build output or other generated files unless repository policy specifically requires them.
- Unrequested changes from another phase or unrelated user work.

## 9. Progress Tracking and Reporting

Maintain progress tracking in the existing project convention if one exists; otherwise use `docs/progress.md`. Create or update this tracker as part of the appropriate documentation/implementation phase, not as an unrelated extra product feature. Do not overwrite a user's existing progress file without reviewing and preserving its information.

For each phase record:

- Phase name and status: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `VERIFICATION FAILED`, or `COMPLETE`.
- Scope/tasks completed and tasks still open.
- Verified facts and evidence links/paths.
- Commands run, tests executed, counts/results, and browser scenarios.
- Failures, root causes, fixes and retests.
- Known issues categorized by severity and impact.
- Blockers requiring user input and the exact question.
- Commit SHA, branch, push status and remote verification.
- Next permitted action (normally the next phase only after full completion).

Distinguish clearly between:

- **Verified:** directly observed or reproduced in this checkout/environment.
- **Assumption/proposal:** plausible but not yet confirmed.
- **Not tested:** verification has not been performed.
- **Blocked:** cannot be verified due to a named missing dependency, permission, environment, or decision.
- **Failed:** attempted and did not meet expectations.

Do not report the whole project as complete while any phase gate remains open.

## 10. Clarification and Blocker Protocol

Pause and ask the user before proceeding when:

- The repository cannot be accessed or the working directory is not the specified repository.
- Existing repository structure or behavior materially conflicts with the proposed plan.
- A decision affects data integrity, security, API compatibility, state transitions, external side effects, user roles, or irreversible behavior and cannot be determined from verified requirements.
- Required credentials, permissions, service availability, model compatibility, or browser tools are unavailable.
- A failure cannot be reliably diagnosed or fixed without risking data loss, security, or unrelated regressions.
- Passing the phase gate would require skipping tests, making a false claim, weakening security, pushing unverified code, or bypassing a requirement.

A blocker report must state what failed, evidence collected, what was attempted, current impact, safe options, and the specific user decision/input needed. Do not continue into the next phase while blocked.

## 11. Definition of Done

AgentFlow is complete only when all approved requirements and all seven phase gates are satisfied; required automated and browser-based checks have actually passed; recovery and security scenarios have been exercised; documentation matches the observed behavior; known issues and limitations are disclosed; progress records are current; and every required phase commit has been pushed and verified on the intended GitHub repository. Anything less must be reported accurately as incomplete or blocked.
