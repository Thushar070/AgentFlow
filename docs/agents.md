# AgentFlow — AI Coding Agent Operating Instructions

These instructions are mandatory for every AI coding agent and every work session in the AgentFlow repository. This file is located at `/AgentFlow/docs/agents.md` and works together with [`plan.md`](./plan.md), which defines the product baseline, architecture proposal, implementation phases, acceptance criteria, verification process, progress tracking, and Git workflow.

**Priority:** Correctness, user-approved scope, security, verified evidence, repository safety, and truthful reporting take priority over speed or apparent progress.

## 1. Mandatory Reading and Session Start

At the beginning of **every new work session**:

1. Read `/AgentFlow/docs/plan.md` from beginning to end (or `docs/plan.md` relative to the verified repository root).
2. Read `/AgentFlow/docs/agents.md` from beginning to end (or `docs/agents.md` relative to the verified repository root).
3. Re-read the current phase and any relevant architecture, verification, Git, and blocker instructions before acting.
4. Confirm the repository root and inspect current Git state, including branch, remote, recent commits, working-tree modifications, and user changes.
5. Read the current progress tracker and relevant project documentation, manifests, tests, migrations, and code before making decisions.
6. Identify the currently authorized phase and its remaining acceptance criteria. Do not assume the previous agent completed a phase merely because a status file says so; verify available evidence. The owner explicitly authorized fresh-project initialization and the deterministic vertical slice in Phase 1, superseding its earlier discovery-only scope.

Do not begin implementation or change project files until these steps are complete. If either instruction file is missing, unreadable, materially contradictory, or differs from the current requested policy, stop and ask the user how to proceed.

## 2. Repository and Factual Accuracy

The target repository is [Thushar070/AgentFlow](https://github.com/Thushar070/AgentFlow) (`https://github.com/Thushar070/AgentFlow.git`). The expected project root is `/AgentFlow/`. Both instruction files must be stored under `/AgentFlow/docs/`: `/AgentFlow/docs/plan.md` and `/AgentFlow/docs/agents.md`. Do not move them to the repository root.

The repository's existing code and exact technology stack were **not verified when these planning files were prepared** because connected GitHub inspection returned `404 Not Found`. Treat all stack items and directory structures described as proposed in `plan.md` as proposals until verified against the actual checkout.

You must:

- Inspect source files, manifests, lockfiles, configuration, migrations, tests, CI, and Git state before asserting what exists.
- Derive available commands and tool capabilities from the repository and environment; never invent commands, packages, test results, browser capabilities, repository permissions, or implementation status.
- Distinguish observed facts from assumptions, proposals, untested behavior, and blockers.
- Never claim a feature, test, build, browser check, commit, push, or deployment succeeded unless it was actually performed and its outcome inspected.
- Never expose secrets while inspecting configuration. Do not print or include credentials in logs, progress reports, commits, or responses.
- Do not silently replace existing working conventions with the proposed architecture or stack. Clarify material conflicts first.

## 3. Scope Discipline and Phase Authorization

Follow `plan.md` exactly. Work on **one phase at a time** and only on tasks belonging to the current authorized phase.

Do not:

- Implement tasks assigned to a later phase early, except for a minimal dependency that is explicitly necessary, documented, and approved.
- Add unrelated features, broad refactors, new frameworks, extra infrastructure, or speculative integrations.
- Redesign stable APIs or data models without a requirement and impact assessment.
- change unrelated formatting or refactor unrelated modules while touching a file.
- introduce AI dependencies into the non-AI workflow engine.
- weaken validation, tests, security, or acceptance criteria to obtain a successful result.

Before changing a file, explain to yourself how the change maps to a specific task in the current phase. Keep the diff focused. If scope expansion is genuinely necessary, document the reason and ask for approval when it materially changes risk, architecture, timeline, dependencies, or external behavior.

## 4. Clarification Is Required for Material Ambiguity

Stop and ask the user rather than guessing when:

- Repository access, branch, working directory, current phase, or target files cannot be established.
- Requirements have multiple materially different interpretations.
- The verified repository conflicts with the plan or with previous decisions.
- A choice affects persistence semantics, migrations, public API compatibility, authentication, authorization, tenant boundaries, approval policy, retries, side effects, data retention, or external service behavior and no approved decision exists.
- An essential integration requires unprovided credentials, provider selection, privacy decisions, or permissions.
- A required test cannot be run, a browser/tool capability is absent without a user-approved alternative, or a platform restriction prevents required verification.
- A safe resolution would require a destructive operation, force push, overwriting user work, weakening a gate, or accepting a meaningful security/correctness risk.

Ask a concise question that names the unknown, why it matters, and the concrete options when possible. While waiting, do not continue work that depends on the answer. Safe, non-destructive inspection or independently authorized work within the same phase may continue only if it does not assume the unresolved decision.

## 5. Architecture and Safety Rules

Apply the architecture and product constraints in `plan.md`. In particular:

- The non-AI workflow product must work without Ollama, a model, or an external LLM API.
- Keep the AI adapter isolated from the core workflow domain and execution engine.
- Model output is untrusted. AI may propose a structured workflow; it must never directly execute generated code, shell commands, raw SQL, or arbitrary functions.
- All actions must come from an explicitly registered catalog and pass server-side schema, policy, and authorization checks.
- Sensitive operations must follow the approved human-approval policy.
- Persistent workflow state is authoritative. Queue messages or successful enqueue calls do not prove a workflow step finished.
- Define and enforce legal state transitions; use transactions and appropriate concurrency controls.
- Treat retries as repeated attempts unless action-specific idempotency guarantees say otherwise. Never claim arbitrary external actions are exactly-once.
- Do not retry every error. Respect documented retryability, limits, timeouts, and recovery rules.
- Handle worker crashes and ambiguous external outcomes through documented reconciliation, idempotency, or manual-review paths.
- Use least privilege, bounded execution, validated inputs, safe error messages, secret redaction, and resource-level authorization.
- Do not commit secrets, credentials, private data, or production records.

## 6. Required Implementation and Verification Loop

For every phase, use this loop and repeat it until all requirements pass or user intervention is required:

1. **Confirm scope:** Check phase status, prerequisites, tasks, acceptance criteria, and pre-existing Git changes.
2. **Implement:** Make the smallest coherent implementation that satisfies the current phase.
3. **Review changes:** Inspect the diff for correctness, scope creep, security, migrations, secret leakage, and compatibility.
4. **Run automated checks:** Execute the repository's actual formatters, linters, type checks, unit tests, integration tests, API tests, end-to-end tests, security checks, and regression tests that apply.
5. **Run the application:** Start the services and processes necessary for the current phase using verified commands and test configuration.
6. **Test as a user:** Open the application in a browser and execute applicable primary and negative user journeys. Use available browser automation when supported. Do not substitute API-only tests for browser verification where browser behavior is part of the acceptance criteria.
7. **Exercise failures and edges:** Test valid and invalid inputs, boundary values, permissions, unavailable services, timeouts, retries, duplicate requests, concurrency, cancellation, interrupted work, and other applicable failure scenarios.
8. **Diagnose failures:** Capture evidence and identify the root cause. Apply a narrow fix, then rerun the relevant failed tests, related integration tests, and regression suite.
9. **Inspect final behavior:** Verify outcomes in the application, database or observable logs where appropriate. Confirm that test-only mocks are not mistaken for proof that real external services work.
10. **Update evidence:** Record actual commands, outcomes, browser scenarios, defects, fixes, unresolved items, and phase status in the progress tracker.
11. **Evaluate the phase gate:** Check every acceptance criterion in `docs/plan.md`. If a required criterion is failed, untested, or blocked, do not call the phase complete.
12. **Commit and push:** Only after all implementation and verification gates pass, create a focused local commit when permitted. Do not push unless the user explicitly authorizes pushing in the current task.
13. **Stop at the gate:** Do not begin the next phase until implementation and verification are complete. Report remote state separately.

Never skip a failing test simply to finish. Do not delete, loosen, mute, or rewrite a test unless an independently valid defect in the test is demonstrated, the change is documented, and the relevant regression checks pass. If reliable verification is impossible, report a blocker and ask the user.

## 7. Browser-Based Verification

Browser verification is mandatory whenever the phase includes a browser-visible feature or user workflow. It must validate real user behavior, including applicable navigation, forms, loading/empty/error states, state updates, permissions, execution controls, approvals, history and refresh behavior.

- Prefer the browser automation capability available in the current environment when supported.
- Confirm the app and required backing services are running before testing.
- Test happy paths and meaningful negative paths, not merely whether the page loads.
- Check browser console errors and failed network requests when the tools permit.
- Record the tested route, actions taken, expected versus actual behavior, and result.
- If browser automation is unavailable, try an available real browser/manual path where possible. If no browser path is available, record the limitation and use an alternative only when the user explicitly approves it. The current fresh-project instruction authorizes the strongest available alternative for this initial slice.
- Never claim browser verification based only on a successful build, unit test, API call, mock, screenshot, or assumption.

## 8. GitHub and Commit/Push Safety

Target remote: `https://github.com/Thushar070/AgentFlow.git` or a verified authenticated equivalent without embedded credentials.

### 8.1 Before committing

Confirm all of the following:

- Current phase implementation is complete.
- Every phase acceptance criterion is satisfied.
- Required unit, integration, end-to-end, security, regression, and browser checks actually passed, or any not-applicable/blocked item has been treated according to the plan's gate rather than silently ignored.
- The application was started and browser-tested where applicable.
- No known blocking defect remains; any material non-blocking issue is documented and accepted as necessary.
- `git diff` and staged changes contain only the current phase's work.
- `git diff --check` and relevant secret scanning/review have been performed as available.
- No unrelated user changes are staged or overwritten.
- The commit message accurately describes the implemented changes.

If any condition is false or unknown, do not commit or push. Continue diagnosis or ask the user.

### 8.2 Commit and push procedure

1. Verify remote, branch, upstream, working-tree status, and intended target.
2. Review the complete diff and stage only files belonging to the completed phase.
3. Run the final required checks against the staged implementation.
4. Commit with a descriptive message, for example `Phase 3: implement deterministic workflow execution`. The actual message must accurately reflect the diff.
5. Push to the approved branch only when the user explicitly authorizes the push in the current task.
6. For an authorized push, verify command success and inspect the remote branch/commit so that the intended commit is confirmed remotely.
7. Record the commit hash, branch, push outcome, and relevant verification evidence. An unapproved push must not be treated as a missing local implementation step.

Never force-push, rewrite shared history, delete branches, reset over user changes, or bypass branch protection without explicit user authorization and an established safe reason. If an explicitly authorized push is blocked by authentication, permission, remote availability, conflict, or policy, do not claim it succeeded; report the evidence. A local commit is not a successful push.

## 9. Failure Recovery and Escalation

When any implementation, tool, test, browser check, service, Git operation, or workflow fails:

1. Stop advancing toward the next phase.
2. Preserve the failure evidence and working-tree state.
3. Reproduce the failure if safe and identify its root cause before broad changes.
4. Attempt a focused, reasonable fix within the current phase and authorized scope.
5. Rerun the failing check, adjacent relevant tests, and regression checks.
6. Update the progress tracker with the failure, root cause, attempted fixes, retest results, and remaining risk.
7. If the issue cannot be reliably resolved, requires a material product decision, or risks data loss/security/regression, stop and ask the user for guidance.

Do not conceal failures, return a false success status, proceed because a deadline is near, introduce an unsafe workaround, or bypass a completion criterion. Do not mutate production data to debug a test unless explicitly approved and safely planned.

## 10. Progress Tracking and Honest Status

Use the existing project progress convention if one is present; otherwise use `/AgentFlow/docs/progress.md` (or `docs/progress.md` relative to the verified repository root) as specified in `docs/plan.md`. Do not overwrite existing user documentation without first reviewing and preserving it. Keep the tracker consistent with the repository state and actual evidence.

For each phase, record:

- Status: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `VERIFICATION FAILED`, or `COMPLETE`.
- Tasks completed and tasks remaining.
- Verified implementation details and evidence paths/links.
- Test commands, environment (without secrets), pass/fail counts, exit status, and concise failure output.
- Browser route and user journey, actual outcome, and tool limitations.
- Defects, root causes, changes, retests, and regressions found.
- Known issues with severity and impact.
- Assumptions, unresolved questions, blockers, and the exact user input needed.
- Commit SHA, branch, push status, and remote verification.

Use status labels precisely:

- **Verified:** Directly inspected or executed successfully in the current checkout/environment.
- **Assumption / Proposed:** Not yet confirmed by repository evidence or user decision.
- **Not tested:** Verification was not performed.
- **Blocked:** Verification or implementation cannot proceed due to a named missing decision, tool, permission, or environment requirement.
- **Failed:** An attempt was made and did not meet expectations.
- **Complete:** Every phase gate in `docs/plan.md`, including Git requirements, passed with evidence.

A successful API test does not imply successful browser verification. A skipped test is not a pass. Do not report a feature as complete merely because code was written.

## 11. Required Communication

At the end of a work session, provide a concise, factual report containing:

1. The current phase and its status.
2. What was changed, with relevant file paths.
3. Exact verification performed and actual outcomes.
4. Browser verification performed or the reason it remains blocked.
5. Failures, known issues, and unresolved questions.
6. Git commit/push status and commit hash, if applicable.
7. The next permitted action or the specific user input required.

Keep verified facts distinct from assumptions. Do not overstate progress, hide failed checks, or describe unimplemented work as delivered.

## 12. Phase Completion Is a Hard Gate

A phase is complete only when **all** of the following are satisfied:

- Its implementation tasks and deliverables are complete.
- Phase-specific acceptance criteria pass.
- Applicable unit, integration, end-to-end, security, and regression tests have actually run and passed.
- The application has been started and browser-based user verification has succeeded wherever applicable.
- Failures have been investigated and fixed, or the phase remains blocked pending user intervention.
- No unresolved blocking defect or undisclosed material risk remains.
- Progress documentation is updated with evidence and honest status.
- The changes have been reviewed and documented. Commit and push status are reported separately; pushing requires explicit user authorization.

If any implementation or verification gate is incomplete, remain in the current phase. Do not start the next phase or claim completion. A remote push is not authorized by default.

## 13. Absolute Priority Rule

When requirements, speed, convenience, or apparent completion conflict, choose correctness, security, verification, user-approved scope, and repository safety. If a requirement cannot be satisfied, explain the blocker with evidence and ask the user what to do. Never silently bypass it.
