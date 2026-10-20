# Action Catalog and Workflow Capabilities

The backend registry in `agentflow/registry.py` is the only action catalog. Signed-in users can read it from `GET /api/actions`; the visual builder uses those schemas, descriptions, categories, timeout/retry metadata, and approval flags directly. Action IDs are versioned contracts. Unknown actions and unknown input fields are rejected by the API before a run is queued and inputs and outputs are validated again by the worker.

## Registered actions

| Action | Inputs | Output (`value` unless shown) | Policy |
| --- | --- | --- | --- |
| `set_value` | `value`: JSON scalar/object/array, at most 10 KB | Configured JSON value | Local, replayable |
| `uppercase` | `value`: bounded string | Uppercase string | Local, replayable |
| `append` | `value`, `suffix`: bounded strings | Concatenated string | Local, replayable |
| `trim_text` | `value`: bounded string | Trimmed string | Local, replayable |
| `replace_text` | `value`, `search`, `replacement`: bounded strings | Literal replacement result | Empty search is a no-op |
| `concatenate_text` | `values`: up to 100 strings; optional `separator` | Joined string | Output bounded to 100 KB |
| `parse_json` | `value`: bounded JSON text | Parsed JSON value | JSON depth 32; malformed input is categorized |
| `get_json_field` | `value`: JSON; `path`: dot path/indexes or JSON Pointer | Selected JSON value | Missing values fail safely |
| `validate_email` | `value`: bounded string | `{valid, reason}` | Syntax check only; no mailbox lookup |
| `validate_url` | `value`; optional `allowed_schemes` | `{valid, reason}` | No network request; HTTPS is default |
| `compare_values` | `left`, `right`, `operator` | `{result}` | Same JSON types for ordering; equality is type-sensitive |
| `filter_records` | `records`, `field`, `operator`, `value` | Matching records, stable order | Missing fields do not match |
| `clean_records` | Bounded records, required fields, trim fields, optional active-only/amount field | `{records, amounts, removed_count}` | Drops incomplete/inactive/non-numeric rows; trims selected text |
| `calculate_total` | `values`: finite numbers or decimal strings | Numeric sum | Empty list totals zero; decimal arithmetic before JSON conversion |
| `http_request` | URL, allowlisted method, optional query/body/approved headers/statuses and timeout | `{status_code, headers, body}` | Exact host/port allowlist; no redirects; no automatic retry; non-replayable |
| `send_webhook` | URL, JSON `payload`, content type, timeout | `{status_code, headers, body}` | Approval required; exact host/port allowlist; one attempt; non-replayable |
| `require_approval` | `value`: bounded JSON | Same value after approval | Pauses until an authorized decision; rejection fails the run |
| `create_result` | `fields`: 1–100 named bounded JSON values | Named object | Output becomes the workflow result |

All actions run in an isolated worker process with an action-specific timeout. Pure actions use bounded retry policies; `http_request` and `send_webhook` use one attempt because an external outcome may be ambiguous. The engine does not promise exactly-once external effects. HTTP response headers are allowlisted and credential-like JSON fields and text patterns are redacted; do not pass credentials as workflow values.

## Workflow inputs and references

Workflow definitions may declare up to 50 named typed `input_schema` fields (`string`, `number`, `integer`, `boolean`, `object`, `array`). The run dialog validates these before queueing. A step may consume an input or a previous step result:

```json
{"$input":"customer"}
{"$input":{"name":"customer","path":"/email"}}
{"$ref":"extract_email"}
{"$ref":{"step":"http_call","path":"/body/items/0/name"}}
```

The short step form selects `/value`. Structured paths use JSON Pointer syntax, are traversed as data only, and reject missing values; there are no expressions or executable templates. Forward/unknown step references, undeclared workflow inputs, malformed paths, duplicate IDs, and over-limit definitions are rejected before queueing. Runtime values are validated against the consuming action's input schema.

## Conditional branches

A `type: "conditional"` step has a `condition` with `left`, `operator`, and `right`, and bounded `then_steps` / `else_steps` action arrays. Operators are `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `is_empty`, and `is_not_empty`. Comparison types and ordering are deterministic; incompatible ordered comparisons fail safely. References in a condition must point to a preceding top-level step or declared run input. Branch action references may use preceding top-level steps or an earlier action in the same branch; cross-branch dependencies are rejected. A branch cannot contain another conditional. Exactly one branch executes, while each action in the other branch is persisted with `skipped` status. The conditional step stores its decision and selected branch result. Branch actions use the same approval, cancellation, retry, timeout, and lease fencing logic as ordinary steps.

The visual editor can add a condition with configured true/false results. Richer branch action editing is currently available through the backend definition contract and the customer-validation template; the editor's condition control currently emits one `set_value` result per branch.

## Templates

Authenticated `GET /api/examples` returns four definitions without creating database rows. The workflow library's **Runnable examples** cards create a user-owned copy:

- **Customer validation** extracts an email, validates syntax, branches on the boolean result, and produces a structured output.
- **API data processing** calls an HTTP endpoint, filters active records, sums amounts, and returns a result. Configure `HTTP_ALLOWED_HOSTS` and `HTTP_ALLOWED_PORTS`; an unlisted destination is rejected.
- **Human approval** pauses on the persisted `require_approval` gate and continues only after approval.
- **Data cleaning pipeline** drops records missing required values or marked inactive, trims selected name/email fields, extracts numeric amounts, totals them, and returns cleaned rows plus the removal count.

Templates create only a workflow definition. They do not insert example users, fake runs, or execution history. Example API input must match its described payload shape. For local API integration tests, set `APP_ENV=test`, use a disposable database, allow only `127.0.0.1` and one dedicated mock port, and bind the mock service to loopback. Never use test mode or its HTTP exception in deployment.

## Outbound HTTP restrictions

Configure exact bare hostnames in `HTTP_ALLOWED_HOSTS` and explicit ports in `HTTP_ALLOWED_PORTS` (default `[443]`). Wildcards, URL userinfo, fragments, loopback/private/link-local/reserved/multicast/metadata IPs, non-HTTPS production URLs, and any DNS answer outside the global address space are rejected. The client resolves and validates all DNS answers, pins the connection to a validated address, disables ambient proxy configuration and redirects, and does not forward application cookies or authorization headers. `APP_ENV=test` permits loopback HTTP only when the exact loopback hostname and port are explicitly allowlisted. Responses have a byte limit (`HTTP_MAX_RESPONSE_BYTES`, default 256 KiB), per-request timeout (maximum 15 seconds), and selected response headers only.

Webhook delivery is approval-required and is not automatically retried. Approval records bind to the run, step, action, workflow version, and canonical resolved input hash. A timeout after delivery may mean the receiver acted without AgentFlow receiving its response; operators must reconcile that outcome with the receiver.

Webhook secret references/signature generation are not implemented: the repository has no encrypted secret store or secret-management API. The safe current behavior is to allow only fixed JSON content type and prevent arbitrary headers; do not put webhook credentials in workflow inputs.
