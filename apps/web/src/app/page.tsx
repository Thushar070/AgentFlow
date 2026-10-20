"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
let csrfToken = "";
const starter = JSON.stringify(
  {
    version: 1,
    name: "Welcome workflow",
    steps: [
      {
        id: "greeting",
        action: "set_value",
        inputs: { value: "hello agentflow" },
      },
      {
        id: "shout",
        action: "uppercase",
        inputs: { value: { $ref: "greeting" } },
      },
    ],
  },
  null,
  2,
);

type Workflow = {
  id: number;
  name: string;
  definition: Record<string, unknown>;
};
type User = { id: number; username: string; email: string; role: string };
type Approval = {
  id: number;
  run_id: number;
  workflow_id: number | null;
  step_id: string | null;
  action: string;
  inputs: Record<string, unknown>;
  status: string;
  requester_id: number | null;
  reason?: string | null;
};
type AuditEvent = {
  id: number;
  event_type: string;
  outcome: string;
  resource_type: string;
  resource_id?: string | null;
  created_at: string;
};
class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
type StepRun = {
  id: string;
  action: string;
  status: string;
  output?: Record<string, unknown> | null;
  error?: string | null;
  error_category?: string | null;
  attempts: {
    attempt_number: number;
    status: string;
    error?: string | null;
    error_category?: string | null;
    started_at: string;
    finished_at?: string | null;
  }[];
};
type Run = {
  id: number;
  workflow_id: number;
  status: string;
  error?: string | null;
  error_category?: string | null;
  started_at?: string | null;
  summary: {
    total_steps: number;
    succeeded_steps: number;
    failed_steps: number;
    not_started_steps: number;
  };
  steps: StepRun[];
};

const activeRunStatuses = new Set([
  "queued",
  "running",
  "retrying",
  "cancel_requested",
  "awaiting_approval",
]);

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (
    init?.method &&
    !["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase()) &&
    csrfToken
  ) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`${API}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      response.status,
    );
  }
  return payload as T;
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [definition, setDefinition] = useState(starter);
  const [usernameOrEmail, setUsernameOrEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("operator");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [apiReady, setApiReady] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const auth = await api<{ user: User; csrf_token: string }>(
        "/api/auth/me",
      );
      csrfToken = auth.csrf_token;
      setUser(auth.user);
      await api<{ status: string }>("/health");
      const [items, recentRuns, pendingApprovals, events] = await Promise.all([
        api<Workflow[]>("/api/workflows"),
        api<Run[]>("/api/runs?limit=20"),
        api<Approval[]>("/api/approvals?status=pending"),
        auth.user.role === "admin"
          ? api<AuditEvent[]>("/api/audit?limit=50")
          : Promise.resolve([] as AuditEvent[]),
      ]);
      setWorkflows(items);
      setRuns(recentRuns);
      setApprovals(pendingApprovals);
      setAuditEvents(events);
      setApiReady(true);
      setError("");
    } catch (cause) {
      const unauthenticated = cause instanceof ApiError && cause.status === 401;
      setApiReady(unauthenticated);
      setUser(null);
      setWorkflows([]);
      setRuns([]);
      setApprovals([]);
      setAuditEvents([]);
      setError(
        unauthenticated
          ? ""
          : cause instanceof Error
            ? cause.message
            : "Could not reach the AgentFlow API.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (
      !runs.some((run) => activeRunStatuses.has(run.status)) &&
      !approvals.some((approval) => approval.status === "pending")
    )
      return;
    const interval = window.setInterval(() => {
      Promise.all([
        api<Run[]>("/api/runs?limit=20"),
        api<Approval[]>("/api/approvals?status=pending"),
      ])
        .then(([recentRuns, pendingApprovals]) => {
          setRuns(recentRuns);
          setApprovals(pendingApprovals);
        })
        .catch((cause: unknown) => {
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not refresh workflow status.",
          );
        });
    }, 1000);
    return () => window.clearInterval(interval);
  }, [runs, approvals]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api<{ user: User; csrf_token: string }>(
        "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify({
            username_or_email: usernameOrEmail,
            password,
          }),
        },
      );
      csrfToken = result.csrf_token;
      setPassword("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    try {
      await api("/api/auth/logout", { method: "POST" });
      csrfToken = "";
      setUser(null);
      setWorkflows([]);
      setRuns([]);
      setApprovals([]);
      setAuditEvents([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign-out failed.");
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(id: number, decision: "approved" | "rejected") {
    setBusy(true);
    try {
      await api(`/api/approvals/${id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      });
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Approval decision failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername,
          email: newEmail,
          password: newPassword,
          role: newRole,
        }),
      });
      setNewUsername("");
      setNewEmail("");
      setNewPassword("");
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Account creation failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const payload = JSON.parse(definition) as Record<string, unknown>;
      const created = await api<Workflow>("/api/workflows", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setWorkflows((current) => [created, ...current]);
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Workflow could not be created.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function runWorkflow(id: number) {
    setBusy(true);
    try {
      const run = await api<Run>(`/api/workflows/${id}/runs`, {
        method: "POST",
      });
      setRuns((current) => [run, ...current]);
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Workflow could not be run.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun(id: number) {
    setBusy(true);
    try {
      const updated = await api<Run>(`/api/runs/${id}/cancel`, {
        method: "POST",
      });
      setRuns((current) =>
        current.map((run) => (run.id === id ? updated : run)),
      );
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Run could not be cancelled.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#home" aria-label="AgentFlow home">
          <span className="brand-mark">A</span>
          <span>agentflow</span>
        </a>
        <div className="topbar-right">
          <span className={`connection ${apiReady ? "online" : "offline"}`}>
            <i /> {apiReady ? "API connected" : "API unavailable"}
          </span>
          {user ? (
            <>
              <span className="avatar" title={user.role}>
                {user.username.slice(0, 2).toUpperCase()}
              </span>
              <button
                className="button secondary"
                onClick={() => void signOut()}
                disabled={busy}
              >
                Sign out
              </button>
            </>
          ) : (
            <span className="avatar">AF</span>
          )}
        </div>
      </header>

      <section className="intro" id="home">
        <div className="eyebrow">
          <span className="spark">✳</span> WORKFLOW STUDIO
        </div>
        <h1>
          Make the busywork
          <br />
          <em>flow.</em>
        </h1>
        <p>
          Build a workflow once. Run it with confidence, and see every step
          along the way.
        </p>
        <div className="intro-meta">
          <span className="status-dot" /> Deterministic automation <b>·</b> AI
          optional
        </div>
      </section>

      {error && (
        <div className="alert" role="alert">
          <span>Something needs attention</span>
          <p>{error}</p>
          <button onClick={() => void refresh()}>Retry connection</button>
        </div>
      )}

      {!user ? (
        <section className="builder auth-panel">
          <div className="section-head">
            <div>
              <div className="eyebrow muted">SECURE WORKSPACE</div>
              <h2>Sign in to AgentFlow</h2>
            </div>
          </div>
          <p className="section-description">
            Accounts are created by an administrator. Public registration is
            disabled.
          </p>
          <form onSubmit={signIn}>
            <label htmlFor="login-identity">Username or email</label>
            <input
              id="login-identity"
              autoComplete="username"
              value={usernameOrEmail}
              onChange={(event) => setUsernameOrEmail(event.target.value)}
              required
            />
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <div className="form-footer">
              <span>Contact your administrator if you need an account.</span>
              <button className="button primary" disabled={busy}>
                Sign in
              </button>
            </div>
          </form>
        </section>
      ) : (
        <>
          {user.role !== "approver" && (
            <>
              <section className="workspace">
                <div className="section-head">
                  <div>
                    <div className="eyebrow muted">YOUR WORKSPACE</div>
                    <h2>Workflow runs</h2>
                  </div>
                  <span className="count">
                    {workflows.length}{" "}
                    {workflows.length === 1 ? "workflow" : "workflows"}
                  </span>
                </div>

                {loading ? (
                  <div className="empty">Connecting to your workspace…</div>
                ) : workflows.length === 0 ? (
                  <div className="empty">
                    <span className="empty-icon">↗</span>
                    <strong>Your first workflow starts here</strong>
                    <p>
                      Create a workflow below, then run it to see each step in
                      action.
                    </p>
                  </div>
                ) : (
                  <div className="workflow-list">
                    {workflows.map((workflow) => (
                      <article className="workflow-card" key={workflow.id}>
                        <div className="flow-icon">⌘</div>
                        <div className="workflow-info">
                          <h3>{workflow.name}</h3>
                          <p>
                            {Array.isArray(workflow.definition.steps)
                              ? workflow.definition.steps.length
                              : 0}{" "}
                            steps <span>·</span> Workflow #{workflow.id}
                          </p>
                        </div>
                        <button
                          className="button primary run-button"
                          disabled={busy || !apiReady}
                          onClick={() => void runWorkflow(workflow.id)}
                        >
                          Run workflow <span>↗</span>
                        </button>
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section className="builder">
                <div className="section-head">
                  <div>
                    <div className="eyebrow muted">WORKFLOW BUILDER</div>
                    <h2>Create a workflow</h2>
                  </div>
                  <span className="version">Schema v1</span>
                </div>
                <p className="section-description">
                  Describe steps in JSON. Each action is validated against the
                  registered action catalog before it can run.
                </p>
                <form onSubmit={createWorkflow}>
                  <label htmlFor="definition">Workflow definition</label>
                  <textarea
                    id="definition"
                    spellCheck={false}
                    value={definition}
                    onChange={(event) => setDefinition(event.target.value)}
                  />
                  <div className="form-footer">
                    <span>
                      Try <code>set_value</code>, <code>uppercase</code>, or{" "}
                      <code>append</code>.
                    </span>
                    <button
                      className="button primary"
                      disabled={busy || !apiReady}
                    >
                      Create workflow <span>＋</span>
                    </button>
                  </div>
                </form>
              </section>

              <section className="runs-section">
                <div className="section-head">
                  <div>
                    <div className="eyebrow muted">LATEST ACTIVITY</div>
                    <h2>Execution history</h2>
                  </div>
                  <span className="count">{runs.length} recent</span>
                </div>
                {runs.length === 0 ? (
                  <div className="empty compact">
                    Queued, completed, failed, and cancelled runs appear here
                    with step-by-step results.
                  </div>
                ) : (
                  <div className="run-list">
                    {runs.map((run) => (
                      <article className="run-card" key={run.id}>
                        <div className="run-title">
                          <span className={`run-state ${run.status}`}>
                            {run.status}
                          </span>
                          <strong>Run #{run.id}</strong>
                          <span className="run-time">
                            {run.started_at
                              ? new Date(run.started_at).toLocaleString()
                              : "Just now"}
                          </span>
                          {activeRunStatuses.has(run.status) &&
                            run.status !== "cancel_requested" && (
                              <button
                                className="button secondary cancel-run"
                                disabled={busy}
                                onClick={() => void cancelRun(run.id)}
                              >
                                Cancel
                              </button>
                            )}
                        </div>
                        <p className="run-summary">
                          {run.summary.succeeded_steps} succeeded ·{" "}
                          {run.summary.failed_steps} failed ·{" "}
                          {run.summary.not_started_steps} not started of{" "}
                          {run.summary.total_steps} steps
                        </p>
                        {run.error && <p className="run-error">{run.error}</p>}
                        <div className="step-list">
                          {run.steps.map((step) => (
                            <div className="step-row" key={step.id}>
                              <span className={`step-check ${step.status}`}>
                                {step.status === "succeeded" ? "✓" : "!"}
                              </span>
                              <span className="step-name">{step.id}</span>
                              <code>{step.action}</code>
                              {step.error_category && (
                                <code>{step.error_category}</code>
                              )}
                              <span className="step-output">
                                {step.error ??
                                  (step.output
                                    ? JSON.stringify(step.output)
                                    : step.status)}
                              </span>
                              {step.attempts.length > 1 && (
                                <small className="attempt-history">
                                  {step.attempts
                                    .map(
                                      (attempt) =>
                                        `#${attempt.attempt_number} ${attempt.status}${
                                          attempt.error_category
                                            ? ` (${attempt.error_category})`
                                            : ""
                                        }`,
                                    )
                                    .join(" · ")}
                                </small>
                              )}
                            </div>
                          ))}
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}

          <section className="runs-section">
            <div className="section-head">
              <div>
                <div className="eyebrow muted">HUMAN REVIEW</div>
                <h2>Approval requests</h2>
              </div>
              <span className="count">{approvals.length} visible</span>
            </div>
            {approvals.length === 0 ? (
              <div className="empty compact">
                No approval requests need attention.
              </div>
            ) : (
              <div className="run-list">
                {approvals.map((approval) => (
                  <article className="run-card" key={approval.id}>
                    <div className="run-title">
                      <span className={`run-state ${approval.status}`}>
                        {approval.status}
                      </span>
                      <strong>Run #{approval.run_id}</strong>
                      <span className="run-time">
                        {approval.action} · {approval.step_id}
                      </span>
                      {user &&
                        ["admin", "approver"].includes(user.role) &&
                        approval.requester_id !== user.id &&
                        approval.status === "pending" && (
                          <>
                            <button
                              className="button primary"
                              disabled={busy}
                              onClick={() =>
                                void decideApproval(approval.id, "approved")
                              }
                            >
                              Approve
                            </button>
                            <button
                              className="button secondary"
                              disabled={busy}
                              onClick={() =>
                                void decideApproval(approval.id, "rejected")
                              }
                            >
                              Reject
                            </button>
                          </>
                        )}
                    </div>
                    <p className="run-summary">
                      Workflow #{approval.workflow_id} · requested by user #
                      {approval.requester_id}
                    </p>
                    <pre className="approval-inputs">
                      {JSON.stringify(approval.inputs, null, 2)}
                    </pre>
                  </article>
                ))}
              </div>
            )}
          </section>

          {user?.role === "admin" && (
            <>
              <section className="builder">
                <div className="section-head">
                  <div>
                    <div className="eyebrow muted">ADMINISTRATION</div>
                    <h2>Create an account</h2>
                  </div>
                </div>
                <p className="section-description">
                  There is no public registration. Share the initial password
                  with the user through a secure channel.
                </p>
                <form onSubmit={createAccount}>
                  <label htmlFor="new-username">Username</label>
                  <input
                    id="new-username"
                    value={newUsername}
                    onChange={(event) => setNewUsername(event.target.value)}
                    required
                    minLength={3}
                    maxLength={80}
                  />
                  <label htmlFor="new-email">Email</label>
                  <input
                    id="new-email"
                    type="email"
                    value={newEmail}
                    onChange={(event) => setNewEmail(event.target.value)}
                    required
                    maxLength={254}
                  />
                  <label htmlFor="new-password">Initial password</label>
                  <input
                    id="new-password"
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    required
                    minLength={12}
                    maxLength={1024}
                  />
                  <label htmlFor="new-role">Role</label>
                  <select
                    id="new-role"
                    value={newRole}
                    onChange={(event) => setNewRole(event.target.value)}
                  >
                    <option value="operator">Operator</option>
                    <option value="approver">Approver</option>
                    <option value="admin">Administrator</option>
                  </select>
                  <div className="form-footer">
                    <span>
                      Password is sent once in the TLS-protected request and is
                      never returned.
                    </span>
                    <button className="button primary" disabled={busy}>
                      Create account
                    </button>
                  </div>
                </form>
              </section>

              <section className="runs-section">
                <div className="section-head">
                  <div>
                    <div className="eyebrow muted">SECURITY HISTORY</div>
                    <h2>Audit events</h2>
                  </div>
                  <span className="count">{auditEvents.length} recent</span>
                </div>
                {auditEvents.length === 0 ? (
                  <div className="empty compact">
                    No audit events recorded yet.
                  </div>
                ) : (
                  <div className="run-list">
                    {auditEvents.map((event) => (
                      <article className="run-card audit-row" key={event.id}>
                        <strong>{event.event_type}</strong>
                        <span>
                          {event.resource_type} #{event.resource_id ?? "—"}
                        </span>
                        <span>{event.outcome}</span>
                        <time>
                          {new Date(event.created_at).toLocaleString()}
                        </time>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </>
      )}

      <footer>
        <span>AgentFlow</span> Reliable workflows, one step at a time.
      </footer>
    </main>
  );
}
