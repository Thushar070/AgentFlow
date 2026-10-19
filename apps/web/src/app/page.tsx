"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
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
type StepRun = {
  id: string;
  action: string;
  status: string;
  output?: Record<string, unknown> | null;
  error?: string | null;
};
type Run = {
  id: number;
  workflow_id: number;
  status: string;
  error?: string | null;
  started_at?: string | null;
  steps: StepRun[];
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return payload as T;
}

export default function Home() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [definition, setDefinition] = useState(starter);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [apiReady, setApiReady] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [items, recentRuns] = await Promise.all([
        api<Workflow[]>("/api/workflows"),
        api<Run[]>("/api/runs?limit=20"),
        api<{ status: string }>("/health"),
      ]);
      setWorkflows(items);
      setRuns(recentRuns);
      setApiReady(true);
      setError("");
    } catch (cause) {
      setApiReady(false);
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not reach the AgentFlow API.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Workflow[]>("/api/workflows"),
      api<Run[]>("/api/runs?limit=20"),
      api<{ status: string }>("/health"),
    ])
      .then(([items, recentRuns]) => {
        if (active) {
          setWorkflows(items);
          setRuns(recentRuns);
          setApiReady(true);
          setError("");
        }
      })
      .catch((cause: unknown) => {
        if (active) {
          setApiReady(false);
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not reach the AgentFlow API.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

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
          <span className="avatar">AF</span>
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
              Create a workflow below, then run it to see each step in action.
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
            <button className="button primary" disabled={busy || !apiReady}>
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
            Completed and failed runs will appear here with step-by-step
            results.
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
                </div>
                {run.error && <p className="run-error">{run.error}</p>}
                <div className="step-list">
                  {run.steps.map((step) => (
                    <div className="step-row" key={step.id}>
                      <span className={`step-check ${step.status}`}>
                        {step.status === "succeeded" ? "✓" : "!"}
                      </span>
                      <span className="step-name">{step.id}</span>
                      <code>{step.action}</code>
                      <span className="step-output">
                        {step.error ??
                          (step.output
                            ? JSON.stringify(step.output)
                            : step.status)}
                      </span>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <footer>
        <span>AgentFlow</span> Reliable workflows, one step at a time.
      </footer>
    </main>
  );
}
