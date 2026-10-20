# Security Design and Threat Model

This document records the Phase 5 security design and its verified limits. AgentFlow runs registered deterministic actions; workflow content and all request fields are untrusted. The application is not a hardened multi-tenant service until the deployment limitations below are addressed.

## Phase 5 implementation notes

### Authentication and sessions

- Use local accounts with a normalized unique email and username, Argon2id password hashes via `argon2-cffi`, and no public registration endpoint.
- Authenticate with an opaque, cryptographically random session token. Store only its SHA-256 digest in the database; validate the session, expiry, and user state on every request. Rotate on login, invalidate on logout/password reset, and expire sessions after a bounded lifetime.
- Send the token only in an `HttpOnly`, `SameSite=Lax` cookie. `Secure` is configurable for local HTTP development and must be enabled behind HTTPS. Cookie-authenticated unsafe methods require a session-bound CSRF token and an allowed browser origin.
- Create the first administrator only through an interactive local CLI command that reads the password with `getpass`, refuses to run once an administrator exists, and never accepts passwords in command-line arguments or logs. A separate local operator password-reset command requires OS access and revokes the target user's sessions.

### Local development endpoints

- The API defaults to `127.0.0.1:8001`; the dashboard is served at `http://localhost:3000`. `CORS_ORIGINS` must include the exact dashboard origin, and `NEXT_PUBLIC_API_URL` must target the API. Next.js reads `NEXT_PUBLIC_API_URL` when its process starts, so restart the dashboard after changing it.
- The earlier frontend fallback/example mismatch to port 8000 was aligned to 8001 in source, environment examples, API defaults, and README. A final targeted scan found no active runtime references to port 8000. Chrome 154 headless then reached the existing dashboard on port 3000 and a disposable API on port 8001; its health request returned 200. Login, logout, role/resource denial, approval, worker enforcement, audit visibility, and refresh behavior passed in that browser run.
- That browser run found and fixed two frontend issues. A normal 401 from /api/auth/me while signed out had been reported as API unavailable; the dashboard now treats it as reachable and displays the sign-in state without an error. The API helper now adds Content-Type: application/json only when a request has a body, so logout no longer sends an empty JSON body that the request middleware rejects. No CORS or CSRF defect was observed; state-changing UI requests passed Origin and CSRF checks.
- Local HTTP development may set `SESSION_COOKIE_SECURE=false` in the local `.env` example. This is only for local HTTP. Use HTTPS and `SESSION_COOKIE_SECURE=true` for deployed environments. Do not expose the development API or dashboard on a public network without the deployment controls below.

### Role and permission matrix

| Capability | admin | operator | approver |
| --- | --- | --- | --- |
| Manage accounts and roles | Yes | No | No |
| Create workflows | Yes | Yes | No |
| Read workflows/runs | All resources | Owned resources | Approval context only |
| Update workflow versions | All resources | Owned resources | No |
| Start/cancel owned runs | Yes | Yes | No |
| Read/decide approval requests | All | Own request status | Pending requests, excluding own |
| Read audit history | Yes | No | No |

The requester cannot approve their own request. Admin role does not bypass a pending approval or the no-self-approval rule. Legacy workflows with no owner remain accessible to administrators only; new workflows are owned by their creator.

### Approval policy

- Approval is configured by action identifier in server configuration and is checked by the worker immediately before an attempt starts.
- Local transformations and `http_request` require no approval by default. `send_webhook` and `require_approval` always require an approval; deployments can designate additional registered actions. Unknown configured action names fail startup/config validation.
- A persisted approval binds one run step, action, immutable workflow version, and canonical hash of resolved inputs. Stale or rejected decisions never authorize execution. Approval decisions and run resumption commit atomically; a conditional state transition prevents duplicate decisions/replays.

### Audit, redaction, limits, dependencies

- Structured audit events are append-only through application APIs and recorded in the same transaction as relevant state changes. Database administrators can still edit/delete rows; no cryptographic immutability is claimed.
- Redaction is recursive for common credential field names and known token patterns at API, audit, diagnostic, and outbound response boundaries. Workflow inputs/outputs are execution data and must remain intact for deterministic downstream steps; therefore raw values can still exist in the database. Do not place credentials in workflow payloads. The application database and backups require OS-level protection.
- Apply a bounded request-body limit, workflow/depth/string limits, run-creation quota, and bounded audit/approval pagination. Avoid a new queue, identity provider, or rate-limit service.
- Runtime dependencies: `argon2-cffi` for Argon2id password hashing, plus `httpx`/`httpcore` for the bounded outbound HTTP client and validated-address transport. Session storage, approvals, audit, and limits use the existing SQLAlchemy database.

### Registered actions and outbound requests

- The backend action registry is authoritative and `GET /api/actions` requires authentication. Actions declare input/output schemas, category, permission metadata, timeout, attempt, replay, and approval policy. Inputs and outputs are validated at API/worker boundaries. Limits include bounded JSON depth and size, definition size, per-action strings/arrays, a 50-step flattened workflow limit, HTTP response size, and HTTP timeout.
- `http_request` and `send_webhook` use an exact administrator-configured host and port allowlist. Wildcards are rejected. The client rejects URL userinfo, fragments, disallowed ports, non-HTTPS non-test requests, and DNS answers that are loopback, private, link-local, reserved, multicast, or otherwise non-global. It pins a validated DNS address for the connection to mitigate rebinding, disables environment proxies and redirects, and forwards no application cookies or authorization headers. Only selected response headers are returned. Text and JSON responses are size-bounded and redacted.
- The only HTTP exception is `APP_ENV=test` with the exact loopback hostname and port explicitly allowlisted for a disposable local mock service. Do not use this mode or allowlist on a deployed service. Production should keep the default empty host list until a trusted integration is explicitly configured.
- HTTP and webhook actions use one attempt and are marked non-replayable. Webhooks additionally require approval. A timeout after a receiver acted but before AgentFlow persisted the response is ambiguous; no exactly-once delivery or automatic reconciliation is claimed. Attempt state is persisted, but there is no external receiver's transaction/idempotency protocol.
- Four authenticated workflow templates are available from `GET /api/examples`; the endpoint returns definitions only and creates no users, workflows, or runs. The API example succeeds only when its destination is on the server allowlist.

## Threat model and initial findings

| Threat | Phase 5 control/evidence |
| --- | --- |
| Unauthenticated or cross-user access | Database-backed sessions, roles, owner-scoped workflow/run queries, and negative API tests. |
| Workflow/action tampering and unregistered actions | Immutable workflow versions, action registry validation, approval input/version binding, worker-side approval check. |
| Forged, stale, duplicate, or replayed approval | Session/role/CSRF checks, single pending decision transition, unique request per run step, canonical input hash. |
| Unauthorized cancel/update/run/account changes | Server-side role and owner checks on each route. |
| Secret leakage and log injection | Recursive API/audit redaction, safe generic client errors, structured metadata only, bounded inputs. |
| Denial of service through payloads/repeated runs/history | Body and nesting caps, existing definition/action limits, per-user run quota, maximum query page sizes. |
| Outbound SSRF through workflow URLs | Exact host and port allowlists, HTTPS outside test, DNS address checks and connection pinning, no redirects/proxies, bounded responses/timeouts; local HTTP is test-only. |
| Duplicate/ambiguous external side effect | External actions have one attempt, are marked non-replayable, and webhook requires approval; receiver-side idempotency/reconciliation is not implemented. |
| Database/audit tampering by an OS or DB administrator | Not prevented cryptographically. Restrict database and backup filesystem access; audit rows are not tamper-proof against an administrator. |

## Deployment limits

- HTTPS termination is required for `Secure` session cookies outside local development. Keep `SESSION_COOKIE_SECURE` enabled in deployments.
- Do not trust identity headers from arbitrary clients. This implementation uses local password authentication and does not trust a reverse proxy identity by default.
- Session tokens are opaque and stored hashed, but users and operators with direct database access can alter application state. SQLite file access is a security boundary.
- Execution payloads and results are persisted to support deterministic replay and history. Recursive redaction protects API/audit/log output but cannot safely remove arbitrary values from execution records without changing workflow semantics.
- Outbound actions are restricted by configured host/port allowlists but their receiver-side idempotency and reconciliation are not implemented. A request timeout can leave the external outcome unknown; inspect the receiver before manually rerunning.
