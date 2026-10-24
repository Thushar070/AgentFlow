"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cachedStaticApiRead } from "../lib/static-api-cache";
import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
let csrfToken = "";
const proposalModels = [
  {
    id: "freellmapi",
    label: "FreeLLMAPI · automatic model routing",
    description:
      "Uses the FreeLLMAPI unified key and its active fallback chain to route across enabled models automatically.",
  },
] as const;
type ProposalModelId = (typeof proposalModels)[number]["id"];
type User = { id: number; username: string; email: string; role: string };
type FieldSchema = {
  type?: string;
  title?: string;
  description?: string;
  anyOf?: FieldSchema[];
  enum?: unknown[];
  items?: FieldSchema;
  additionalProperties?: FieldSchema | boolean;
  default?: unknown;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
};
type Action = {
  id: string;
  name: string;
  category: string;
  version: string;
  description: string;
  input_schema: {
    properties?: Record<string, FieldSchema>;
    required?: string[];
  };
  output_schema?: { properties?: Record<string, FieldSchema> };
  approval_required: boolean;
  timeout_seconds: number;
  max_attempts: number;
};
type DefinitionStep = {
  id: string;
  type?: string;
  action: string;
  inputs: Record<string, unknown>;
};
type Workflow = {
  id: number;
  name: string;
  description?: string | null;
  definition: {
    version: number;
    name: string;
    description?: string;
    input_schema?: Record<string, WorkflowInputField>;
    steps: DefinitionStep[];
  };
  current_version_number: number;
  created_at?: string;
  updated_at?: string;
};
type ExampleTemplate = {
  id: string;
  name: string;
  description: string;
  version: number;
  input_schema?: Record<string, WorkflowInputField>;
  steps: Record<string, unknown>[];
};
type Attempt = {
  attempt_number: number;
  status: string;
  error?: string | null;
  error_category?: string | null;
  started_at: string;
  finished_at?: string | null;
};
type StepRun = {
  id: string;
  action: string;
  status: string;
  inputs?: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  error?: string | null;
  error_category?: string | null;
  attempts: Attempt[];
};
type Run = {
  id: number;
  workflow_id: number;
  workflow_version_number: number;
  status: string;
  error?: string | null;
  error_category?: string | null;
  started_at?: string | null;
  created_at: string;
  finished_at?: string | null;
  summary: {
    total_steps: number;
    succeeded_steps: number;
    failed_steps: number;
    not_started_steps: number;
  };
  steps: StepRun[];
  input?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
};
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
  created_at?: string;
  decided_at?: string | null;
};
type AuditEvent = {
  id: number;
  event_type: string;
  outcome: string;
  resource_type: string;
  resource_id?: string | null;
  actor_id?: number | null;
  created_at: string;
};
class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
const activeStatuses = new Set([
  "queued",
  "running",
  "retrying",
  "cancel_requested",
  "awaiting_approval",
]);
const navItems = [
  ["Dashboard", "/dashboard", "◫"],
  ["Workflows", "/workflows", "⌘"],
  ["Runs", "/runs", "↗"],
  ["Approvals", "/approvals", "✓"],
  ["Audit history", "/audit", "≡"],
  ["Accounts", "/accounts", "◎"],
];

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();

  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (
    init?.method &&
    !["GET", "HEAD", "OPTIONS"].includes(init.method.toUpperCase()) &&
    csrfToken
  )
    headers.set("X-CSRF-Token", csrfToken);
  const request = async () => {
    const response = await fetch(`${API}${path}`, {
      ...init,
      signal: init?.signal ?? AbortSignal.timeout(15000),
      credentials: "include",
      headers,
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail;
      const message = Array.isArray(detail)
        ? detail
            .map(
              (item: { loc?: unknown[]; msg?: string }) =>
                `${Array.isArray(item.loc) ? item.loc.join(".") : "Request"}: ${item.msg ?? "Invalid value."}`,
            )
            .join("; ")
        : typeof detail === "string"
          ? detail
          : JSON.stringify(detail);
      throw new ApiError(message, response.status);
    }
    return payload;
  };
  return method === "GET" ? cachedStaticApiRead(path, request) : request();
}

function slugFor(pathname: string) {
  return pathname.split("/").filter(Boolean);
}
function readableAction(action: Action | undefined, id: string) {
  return action?.id === id
    ? action.description.replace(/[.]+$/, "")
    : id.replaceAll("_", " ");
}
function displayAction(id: string, catalog: Action[]) {
  return (
    catalog.find((item) => item.id === id)?.name ?? id.replaceAll("_", " ")
  );
}
function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}
function duration(started?: string | null, finished?: string | null) {
  if (!started || !finished) return "—";
  const elapsed = Math.max(
    0,
    new Date(finished).getTime() - new Date(started).getTime(),
  );
  return elapsed < 1000 ? `${elapsed} ms` : `${(elapsed / 1000).toFixed(2)} s`;
}
function pretty(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

type WorkflowInputField = {
  type: string;
  required: boolean;
  description?: string;
  default?: unknown;
};
type BuilderStep = {
  key: string;
  action: string;
  inputs: Record<string, string>;
  refs: Record<string, string>;
  paths: Record<string, string>;
  conditional?: {
    operator: string;
    left: string;
    right: string;
    thenValue: string;
    elseValue: string;
  };
};
function fromDefinition(definition?: Workflow["definition"]): {
  name: string;
  description: string;
  steps: BuilderStep[];
  inputSchema: Record<string, WorkflowInputField>;
} {
  return {
    name: definition?.name ?? "",
    description: definition?.description ?? "",
    inputSchema: definition?.input_schema ?? {},
    steps: (definition?.steps ?? []).map((step) => {
      if (step.type === "conditional") {
        const conditional = step as unknown as {
          id: string;
          condition: { left: unknown; operator: string; right: unknown };
          then_steps: { inputs: { value?: unknown } }[];
          else_steps: { inputs: { value?: unknown } }[];
        };
        return {
          key: conditional.id,
          action: "",
          inputs: {},
          refs: {},
          paths: {},
          conditional: {
            operator: conditional.condition.operator,
            left: JSON.stringify(conditional.condition.left),
            right: JSON.stringify(conditional.condition.right),
            thenValue: JSON.stringify(conditional.then_steps[0]?.inputs.value),
            elseValue: JSON.stringify(conditional.else_steps[0]?.inputs.value),
          },
        };
      }
      const inputs: Record<string, string> = {};
      const refs: Record<string, string> = {};
      const paths: Record<string, string> = {};
      for (const [key, value] of Object.entries(step.inputs ?? {})) {
        if (value && typeof value === "object" && "$ref" in value) {
          const reference = (value as { $ref: unknown }).$ref;
          if (typeof reference === "string") {
            refs[key] = `step:${reference}`;
            paths[key] = "/value";
          } else if (reference && typeof reference === "object") {
            const ref = reference as { step?: string; path?: string };
            refs[key] = `step:${ref.step ?? ""}`;
            paths[key] = ref.path ?? "/value";
          }
        } else if (value && typeof value === "object" && "$input" in value) {
          const reference = (value as { $input: unknown }).$input;
          if (typeof reference === "string") refs[key] = `input:${reference}`;
          else if (reference && typeof reference === "object") {
            const ref = reference as { name?: string; path?: string };
            refs[key] = `input:${ref.name ?? ""}`;
            paths[key] = ref.path ?? "";
          }
        } else {
          inputs[key] =
            typeof value === "string" ? value : JSON.stringify(value ?? "");
        }
      }
      return { key: step.id, action: step.action, inputs, refs, paths };
    }),
  };
}
function buildInputs(
  step: BuilderStep,
  action?: Action,
): Record<string, unknown> {
  const inputs: Record<string, unknown> = {};
  for (const key of new Set([
    ...Object.keys(step.inputs),
    ...Object.keys(step.refs),
  ])) {
    const source = step.refs[key];
    if (source?.startsWith("step:")) {
      inputs[key] = {
        $ref: { step: source.slice(5), path: step.paths[key] || "/value" },
      };
    } else if (source?.startsWith("input:")) {
      const name = source.slice(6);
      inputs[key] = step.paths[key]
        ? { $input: { name, path: step.paths[key] } }
        : { $input: name };
    } else {
      const raw = step.inputs[key] ?? "";
      const schema = action?.input_schema.properties?.[key];
      if (!raw.trim() && schema?.default === undefined) continue;
      try {
        inputs[key] = parseBuilderValue(raw, schema);
      } catch {
        inputs[key] = raw;
      }
    }
  }
  return inputs;
}
function parseBuilderValue(raw: string, schema?: FieldSchema): unknown {
  if (schema?.type === "string") return raw;
  if (schema?.type === "boolean") return raw === "true";
  if (schema?.type === "number" || schema?.type === "integer") {
    return raw.trim() === "" ? undefined : Number(raw);
  }
  if (raw.trim() === "") return schema?.default;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export default function Home() {
  const pathname = usePathname();
  const router = useRouter();
  const parts = useMemo(() => slugFor(pathname), [pathname]);
  const section = parts[0] || "dashboard";
  const routeId = Number(parts[1]);
  const [user, setUser] = useState<User | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [examples, setExamples] = useState<ExampleTemplate[]>([]);
  const [apiReady, setApiReady] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [identity, setIdentity] = useState("");
  const [password, setPassword] = useState("");
  const [approvalReasons, setApprovalReasons] = useState<
    Record<number, string>
  >({});
  const [tutorialDismissed, setTutorialDismissed] = useState(false);
  const [auditOffset, setAuditOffset] = useState(0);
  const [canLoadMoreAudit, setCanLoadMoreAudit] = useState(false);
  const [workflowName, setWorkflowName] = useState("");
  const [workflowDescription, setWorkflowDescription] = useState("");
  const [proposalPrompt, setProposalPrompt] = useState("");
  const [proposalModel, setProposalModel] =
    useState<ProposalModelId>("freellmapi");
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState("");
  const [proposalReady, setProposalReady] = useState(false);
  const [workflowInputSchema, setWorkflowInputSchema] = useState<
    Record<string, WorkflowInputField>
  >({});
  const [workflowInputNameDrafts, setWorkflowInputNameDrafts] = useState<
    Record<string, string>
  >({});
  const [builderSteps, setBuilderSteps] = useState<BuilderStep[]>([]);
  const [runInputWorkflow, setRunInputWorkflow] = useState<Workflow | null>(
    null,
  );
  const [runInputValues, setRunInputValues] = useState<Record<string, string>>(
    {},
  );
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [validation, setValidation] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("operator");
  const runKeys = useRef(new Map<number, string>());
  const report = useCallback((cause: unknown, fallback: string) => {
    const offline =
      cause instanceof TypeError ||
      (cause instanceof DOMException &&
        ["AbortError", "TimeoutError"].includes(cause.name));
    if (offline) setApiReady(false);
    setError(
      offline
        ? "The API is unavailable or the request timed out. Your unsent form values are still here."
        : cause instanceof Error
          ? cause.message
          : fallback,
    );
  }, []);
  const refresh = useCallback(async () => {
    setLoading(true);
    setApiReady(null);
    try {
      const auth = await api<{ user: User; csrf_token: string }>(
        "/api/auth/me",
      );
      csrfToken = auth.csrf_token;
      setUser(auth.user);
      const [wf, recent, approvalsList, audit, catalog, templates] =
        await Promise.all([
          api<Workflow[]>("/api/workflows"),
          api<Run[]>("/api/runs?limit=50"),
          api<Approval[]>("/api/approvals?limit=100"),
          auth.user.role === "admin"
            ? api<AuditEvent[]>("/api/audit?limit=50&offset=0")
            : Promise.resolve([] as AuditEvent[]),
          api<Action[]>("/api/actions"),
          api<ExampleTemplate[]>("/api/examples"),
        ]);
      setWorkflows(wf);
      setRuns(recent);
      setApprovals(approvalsList);
      setEvents(audit);
      setActions(catalog);
      setExamples(templates);
      setAuditOffset(0);
      setCanLoadMoreAudit(auth.user.role === "admin" && audit.length === 50);
      setApiReady(true);
      setError("");
    } catch (cause) {
      const unauth = cause instanceof ApiError && cause.status === 401;
      setApiReady(unauth);
      setUser(null);
      setWorkflows([]);
      setRuns([]);
      setApprovals([]);
      setEvents([]);
      setActions([]);
      setExamples([]);
      if (!unauth) report(cause, "Could not reach the AgentFlow API.");
      else setError("");
    } finally {
      setLoading(false);
    }
  }, [report]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    if (!user) return;
    setTutorialDismissed(
      window.localStorage.getItem(`agentflow-tutorial-dismissed-${user.id}`) ===
        "true",
    );
  }, [user?.id]);
  useEffect(() => {
    if (section !== "runs" || !Number.isFinite(routeId) || routeId < 1) return;
    let cancelled = false;
    void api<Run>(`/api/runs/${routeId}`)
      .then((run) => {
        if (!cancelled)
          setRuns((current) => [
            run,
            ...current.filter((item) => item.id !== run.id),
          ]);
      })
      .catch((cause) => {
        if (!cancelled) report(cause, "This run could not be loaded.");
      });
    return () => {
      cancelled = true;
    };
  }, [section, routeId, report]);
  useEffect(() => {
    if (
      !runs.some((run) => activeStatuses.has(run.status)) &&
      !approvals.some((item) => item.status === "pending")
    )
      return;
    const interval = window.setInterval(() => {
      void Promise.all([
        api<Run[]>("/api/runs?limit=50"),
        api<Approval[]>("/api/approvals?limit=100"),
      ])
        .then(([r, a]) => {
          setRuns(r);
          setApprovals((current) => {
            const decided = current.filter((item) => item.status !== "pending");
            return [...decided, ...a];
          });
        })
        .catch((cause) => report(cause, "Could not refresh workflow status."));
    }, 2500);
    return () => window.clearInterval(interval);
  }, [runs, approvals, report]);
  useEffect(() => {
    if (section !== "workflows" || parts[1] !== "new") {
      setEditingId(null);
      return;
    }
  }, [section, parts]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api<{ csrf_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username_or_email: identity, password }),
      });
      csrfToken = result.csrf_token;
      setPassword("");
      await refresh();
      router.replace("/dashboard");
    } catch (cause) {
      report(cause, "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }
  async function signOut() {
    setBusy(true);
    try {
      await api("/api/auth/logout", { method: "POST" });
      csrfToken = "";
      window.location.assign("/");
    } catch (cause) {
      report(cause, "Sign-out failed.");
    } finally {
      setBusy(false);
    }
  }
  function resetBuilder() {
    setEditingId(null);
    setWorkflowName("");
    setWorkflowDescription("");
    setWorkflowInputSchema({});
    setWorkflowInputNameDrafts({});
    setBuilderSteps([]);
    setValidation("");
    setProposalPrompt("");
    setProposalError("");
    setProposalReady(false);
  }
  function startEdit(workflow: Workflow) {
    const unsupportedBranch = workflow.definition.steps.some((step) => {
      if (step.type !== "conditional") return false;
      const conditional = step as unknown as {
        then_steps: { action: string }[];
        else_steps: { action: string }[];
      };
      return (
        conditional.then_steps.length !== 1 ||
        conditional.else_steps.length !== 1 ||
        conditional.then_steps[0]?.action !== "set_value" ||
        conditional.else_steps[0]?.action !== "set_value"
      );
    });
    if (unsupportedBranch) {
      setValidation(
        "The visual editor can edit conditions with one Set a value result on each branch. This workflow has a richer branch; its saved definition remains available in Advanced view.",
      );
      return;
    }
    setValidation("");
    router.push(`/workflows/${workflow.id}/edit`);
  }
  function addStep(actionId?: string) {
    const action = actionId ?? actions[0]?.id;
    if (!action) return;
    const actionMeta = actions.find((item) => item.id === action);
    const inputs = Object.fromEntries(
      Object.entries(actionMeta?.input_schema.properties ?? {}).map(
        ([key, schema]) => [
          key,
          schema.default === undefined ? "" : JSON.stringify(schema.default),
        ],
      ),
    );
    setBuilderSteps((steps) => [
      ...steps,
      { key: `step_${steps.length + 1}`, action, inputs, refs: {}, paths: {} },
    ]);
    setValidation("");
  }
  function addConditional() {
    setBuilderSteps((steps) => [
      ...steps,
      {
        key: `condition_${steps.length + 1}`,
        action: "",
        inputs: {},
        refs: {},
        paths: {},
        conditional: {
          operator: "eq",
          left: "",
          right: "",
          thenValue: "Condition was true",
          elseValue: "Condition was false",
        },
      },
    ]);
  }
  function changeCondition(index: number, key: string, value: string) {
    setBuilderSteps((steps) =>
      steps.map((step, i) =>
        i === index && step.conditional
          ? { ...step, conditional: { ...step.conditional, [key]: value } }
          : step,
      ),
    );
  }
  function changeAction(index: number, actionId: string) {
    const actionMeta = actions.find((item) => item.id === actionId);
    const inputs = Object.fromEntries(
      Object.entries(actionMeta?.input_schema.properties ?? {}).map(
        ([key, schema]) => [
          key,
          schema.default === undefined ? "" : JSON.stringify(schema.default),
        ],
      ),
    );
    setBuilderSteps((steps) =>
      steps.map((step, i) =>
        i === index
          ? { ...step, action: actionId, inputs, refs: {}, paths: {} }
          : step,
      ),
    );
  }
  function changeInput(index: number, key: string, value: string) {
    setBuilderSteps((steps) =>
      steps.map((step, i) =>
        i === index
          ? {
              ...step,
              inputs: { ...step.inputs, [key]: value },
              refs: { ...step.refs, [key]: "" },
            }
          : step,
      ),
    );
  }
  function changeRef(index: number, key: string, value: string) {
    setBuilderSteps((steps) =>
      steps.map((step, i) =>
        i === index ? { ...step, refs: { ...step.refs, [key]: value } } : step,
      ),
    );
  }
  function changeRefPath(index: number, key: string, value: string) {
    setBuilderSteps((steps) =>
      steps.map((step, i) =>
        i === index
          ? { ...step, paths: { ...step.paths, [key]: value } }
          : step,
      ),
    );
  }
  function updateWorkflowInput(
    oldName: string,
    name: string,
    field: WorkflowInputField,
  ) {
    setWorkflowInputSchema((current) => {
      const updated = { ...current };
      if (oldName !== name) delete updated[oldName];
      if (name) updated[name] = field;
      return updated;
    });
    if (oldName !== name) {
      setBuilderSteps((steps) =>
        steps.map((step) => ({
          ...step,
          refs: Object.fromEntries(
            Object.entries(step.refs).map(([key, source]) => [
              key,
              source === `input:${oldName}` ? `input:${name}` : source,
            ]),
          ),
        })),
      );
      setWorkflowInputNameDrafts((drafts) => {
        const next = { ...drafts };
        delete next[oldName];
        return next;
      });
    }
  }
  function commitWorkflowInputName(name: string) {
    const draft = workflowInputNameDrafts[name];
    if (draft === undefined) return;
    const nextName = draft.replace(/[^a-zA-Z0-9_-]/g, "_");
    if (!nextName || (nextName !== name && workflowInputSchema[nextName])) {
      setValidation(
        "Input names must be unique and contain only letters, numbers, underscores, or hyphens.",
      );
      setWorkflowInputNameDrafts((current) => ({ ...current, [name]: name }));
      return;
    }
    updateWorkflowInput(name, nextName, workflowInputSchema[name]);
  }
  function addWorkflowInput() {
    let name = `input_${Object.keys(workflowInputSchema).length + 1}`;
    while (workflowInputSchema[name]) name += "_";
    setWorkflowInputSchema((current) => ({
      ...current,
      [name]: { type: "string", required: true },
    }));
  }
  function moveStep(index: number, delta: number) {
    const dest = index + delta;
    if (dest < 0 || dest >= builderSteps.length) return;
    const reordered = [...builderSteps];
    [reordered[index], reordered[dest]] = [reordered[dest], reordered[index]];
    setBuilderSteps(reordered);
  }
  const generatedDefinition = useMemo(
    () => ({
      version: 1,
      name: workflowName.trim(),
      ...(workflowDescription.trim()
        ? { description: workflowDescription.trim() }
        : {}),
      ...(Object.keys(workflowInputSchema).length
        ? { input_schema: workflowInputSchema }
        : {}),
      steps: builderSteps.map((step) =>
        step.conditional
          ? {
              id: step.key,
              type: "conditional",
              condition: {
                left: parseBuilderValue(step.conditional.left),
                operator: step.conditional.operator,
                right: parseBuilderValue(step.conditional.right),
              },
              then_steps: [
                {
                  id: `${step.key}_then`,
                  action: "set_value",
                  inputs: {
                    value: parseBuilderValue(step.conditional.thenValue),
                  },
                },
              ],
              else_steps: [
                {
                  id: `${step.key}_else`,
                  action: "set_value",
                  inputs: {
                    value: parseBuilderValue(step.conditional.elseValue),
                  },
                },
              ],
            }
          : {
              id: step.key,
              action: step.action,
              inputs: buildInputs(
                step,
                actions.find((item) => item.id === step.action),
              ),
            },
      ),
    }),
    [
      workflowName,
      workflowDescription,
      workflowInputSchema,
      builderSteps,
      actions,
    ],
  );
  function checkBuilder() {
    if (!workflowName.trim()) return "Enter a workflow name.";
    if (workflowName.trim().length > 200)
      return "Workflow names must be 200 characters or fewer.";
    if (!builderSteps.length) return "Add at least one action step.";
    for (const [index, step] of builderSteps.entries()) {
      if (step.conditional) {
        if (!step.conditional.left.trim() || !step.conditional.right.trim())
          return `Condition ${index + 1}: enter both comparison values.`;
        try {
          parseBuilderValue(step.conditional.left);
          parseBuilderValue(step.conditional.right);
        } catch {
          return `Condition ${index + 1}: enter valid comparison values.`;
        }
        continue;
      }
      const spec = actions.find((item) => item.id === step.action);
      if (!spec) return `Step ${index + 1}: choose an available action.`;
      const fields = spec.input_schema.properties ?? {};
      for (const field of spec.input_schema.required ?? []) {
        const schema = fields[field];
        const text = step.inputs[field] ?? "";
        if (
          !step.refs[field] &&
          !text.trim() &&
          (schema?.type !== "string" || (schema.minLength ?? 0) > 0)
        )
          return `Step ${index + 1}: enter ${field.replaceAll("_", " ")}.`;
        if (step.refs[field]?.startsWith("step:")) {
          const source = step.refs[field].slice(5);
          if (
            !builderSteps.slice(0, index).some((prior) => prior.key === source)
          )
            return `Step ${index + 1}: choose an earlier step output.`;
        } else if (
          step.refs[field]?.startsWith("input:") &&
          !workflowInputSchema[step.refs[field].slice(6)]
        ) {
          return `Step ${index + 1}: choose a declared workflow input.`;
        }
        if (!step.refs[field] && text.trim()) {
          try {
            const parsed = parseBuilderValue(text, schema);
            if (schema?.type === "number" && typeof parsed !== "number")
              return `Step ${index + 1}: enter a number for ${field}.`;
            if (schema?.type === "integer" && !Number.isInteger(parsed))
              return `Step ${index + 1}: enter a whole number for ${field}.`;
            if (schema?.enum && !schema.enum.includes(parsed))
              return `Step ${index + 1}: choose a supported value for ${field}.`;
          } catch {
            return `Step ${index + 1}: enter valid structured data for ${field}.`;
          }
        }
      }
    }
    return "";
  }
  async function saveWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const localError = checkBuilder();
    setValidation(localError);
    if (localError) return;
    setBusy(true);
    try {
      const saved = await api<Workflow>(
        editingId ? `/api/workflows/${editingId}` : "/api/workflows",
        {
          method: editingId ? "PUT" : "POST",
          body: JSON.stringify(generatedDefinition),
        },
      );
      setWorkflows((items) =>
        editingId
          ? items.map((item) => (item.id === saved.id ? saved : item))
          : [saved, ...items],
      );
      resetBuilder();
      router.push("/workflows");
    } catch (cause) {
      report(cause, "Workflow could not be saved.");
    } finally {
      setBusy(false);
    }
  }
  async function generateWorkflowProposal() {
    setProposalError("");
    if (!proposalPrompt.trim()) {
      setProposalError("Describe the workflow you want to create.");
      return;
    }
    setProposalBusy(true);
    try {
      const proposed = await api<Workflow["definition"]>(
        "/api/workflows/proposals",
        {
          method: "POST",
          body: JSON.stringify({
            prompt: proposalPrompt.trim(),
            model_id: proposalModel,
          }),
          signal: AbortSignal.timeout(310_000),
        },
      );
      const editable = fromDefinition(proposed);
      setEditingId(null);
      setWorkflowName(editable.name);
      setWorkflowDescription(editable.description);
      setWorkflowInputSchema(editable.inputSchema);
      setWorkflowInputNameDrafts({});
      setBuilderSteps(editable.steps);
      setValidation("");
      setProposalReady(true);
    } catch (cause) {
      setProposalReady(false);
      setProposalError(
        cause instanceof Error
          ? cause.message
          : "AgentFlow could not generate a workflow proposal.",
      );
    } finally {
      setProposalBusy(false);
    }
  }
  async function createFromExample(template: ExampleTemplate) {
    setBusy(true);
    try {
      const definition = Object.fromEntries(
        Object.entries(template).filter(([key]) => key !== "id"),
      );
      const saved = await api<Workflow>("/api/workflows", {
        method: "POST",
        body: JSON.stringify(definition),
      });
      setWorkflows((items) => [saved, ...items]);
      router.push(`/workflows/${saved.id}`);
    } catch (cause) {
      report(cause, "Example workflow could not be created.");
    } finally {
      setBusy(false);
    }
  }
  async function submitWorkflowRun(
    id: number,
    input: Record<string, unknown> = {},
  ) {
    setBusy(true);
    try {
      const key = runKeys.current.get(id) ?? crypto.randomUUID();
      runKeys.current.set(id, key);
      const run = await api<Run>(`/api/workflows/${id}/runs`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: Object.keys(input).length ? JSON.stringify({ input }) : undefined,
      });
      runKeys.current.delete(id);
      setRuns((current) => [
        run,
        ...current.filter((item) => item.id !== run.id),
      ]);
      setRunInputWorkflow(null);
      setRunInputValues({});
      router.push(`/runs/${run.id}`);
    } catch (cause) {
      report(cause, "Workflow could not be run.");
    } finally {
      setBusy(false);
    }
  }
  async function runWorkflow(id: number) {
    const workflow = workflows.find((item) => item.id === id);
    const schema = workflow?.definition.input_schema ?? {};
    if (workflow && Object.keys(schema).length) {
      setRunInputWorkflow(workflow);
      setRunInputValues(
        Object.fromEntries(
          Object.entries(schema).map(([key, item]) => [
            key,
            item.default === undefined ? "" : JSON.stringify(item.default),
          ]),
        ),
      );
      return;
    }
    await submitWorkflowRun(id);
  }
  async function submitRunInput(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!runInputWorkflow) return;
    const input: Record<string, unknown> = {};
    try {
      for (const [name, field] of Object.entries(
        runInputWorkflow.definition.input_schema ?? {},
      )) {
        const raw = runInputValues[name] ?? "";
        if (!raw.trim() && !field.required && field.default === undefined)
          continue;
        if (!raw.trim() && field.type !== "string")
          throw new Error(`Enter ${name.replaceAll("_", " ")}.`);
        input[name] = parseBuilderValue(raw, { type: field.type });
      }
    } catch (cause) {
      report(cause, "Run input is invalid.");
      return;
    }
    await submitWorkflowRun(runInputWorkflow.id, input);
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
    } catch (cause) {
      report(cause, "Run could not be cancelled.");
    } finally {
      setBusy(false);
    }
  }
  async function decideApproval(id: number, decision: "approved" | "rejected") {
    setBusy(true);
    try {
      const updated = await api<Approval>(`/api/approvals/${id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, reason: approvalReasons[id] || null }),
      });
      setApprovals((current) =>
        current.map((item) => (item.id === id ? updated : item)),
      );
    } catch (cause) {
      report(cause, "Approval decision failed.");
    } finally {
      setBusy(false);
    }
  }
  async function loadOlderAudit() {
    const nextOffset = auditOffset + 50;
    setBusy(true);
    try {
      const page = await api<AuditEvent[]>(
        `/api/audit?limit=50&offset=${nextOffset}`,
      );
      setEvents((current) => [...current, ...page]);
      setAuditOffset(nextOffset);
      setCanLoadMoreAudit(page.length === 50);
    } catch (cause) {
      report(cause, "Audit history could not be loaded.");
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
      setError("Account created.");
    } catch (cause) {
      report(cause, "Account creation failed.");
    } finally {
      setBusy(false);
    }
  }
  async function runTutorial() {
    setBusy(true);
    setError("");
    const definition = {
      version: 1,
      name: "Text Transformation",
      description: "Turn a greeting into uppercase text.",
      steps: [
        {
          id: "source",
          action: "set_value",
          inputs: { value: "hello agentflow" },
        },
        {
          id: "uppercase",
          action: "uppercase",
          inputs: { value: { $ref: "source" } },
        },
      ],
    };
    try {
      let workflow = workflows.find(
        (item) =>
          item.name === definition.name &&
          JSON.stringify(item.definition.steps) ===
            JSON.stringify(definition.steps),
      );
      if (!workflow)
        workflow = await api<Workflow>("/api/workflows", {
          method: "POST",
          body: JSON.stringify(definition),
        });
      setWorkflows((current) =>
        current.some((item) => item.id === workflow!.id)
          ? current
          : [workflow!, ...current],
      );
      await runWorkflow(workflow.id);
    } catch (cause) {
      report(cause, "The example workflow could not be run.");
    } finally {
      setBusy(false);
    }
  }

  const runDetail = runs.find((run) => run.id === routeId);
  const workflowDetail = workflows.find((workflow) => workflow.id === routeId);
  const pendingApprovalCount = approvals.filter(
    (item) => item.status === "pending",
  ).length;
  const nav = user
    ? navItems
        .filter(
          ([label]) =>
            (label !== "Accounts" && label !== "Audit history") ||
            user.role === "admin",
        )
        .filter(
          ([label]) =>
            (label !== "Workflows" && label !== "Runs") ||
            user.role !== "approver",
        )
    : [];
  const builderPath =
    section === "workflows" && (parts[1] === "new" || parts[2] === "edit");
  const editQuery = builderPath && parts[2] === "edit" ? parts[1] : null;
  const loginRoute = !user && !loading;

  useEffect(() => {
    if (!editQuery) return;
    const id = Number(editQuery);
    if (!Number.isInteger(id) || id < 1) return;
    let cancelled = false;
    setLoadingEdit(true);
    void api<Workflow>(`/api/workflows/${id}`)
      .then((workflow) => {
        if (cancelled) return;
        const draft = fromDefinition(workflow.definition);
        setEditingId(workflow.id);
        setWorkflowName(draft.name);
        setWorkflowDescription(draft.description);
        setWorkflowInputSchema(draft.inputSchema);
        setBuilderSteps(draft.steps);
      })
      .catch((cause) => {
        if (!cancelled)
          report(cause, "This workflow could not be opened for editing.");
      })
      .finally(() => {
        if (!cancelled) setLoadingEdit(false);
      });
    return () => {
      cancelled = true;
    };
  }, [editQuery, report]);

  return loginRoute ? (
    <main className="login-screen">
      <section className="login-card">
        <Link href="/" className="brand">
          <span className="brand-mark">A</span>
          <span>AgentFlow</span>
        </Link>
        <span
          className={`connection ${apiReady === null ? "checking" : apiReady ? "online" : "offline"}`}
        >
          <i />
          {apiReady === null
            ? "Checking API…"
            : apiReady
              ? "API connected"
              : "API unavailable"}
        </span>
        <div className="eyebrow">SECURE WORKSPACE</div>
        <h1>Sign in</h1>
        <p>
          Build reliable, step-by-step workflows and inspect every run. Accounts
          are created by your administrator.
        </p>
        {error && (
          <div className="alert" role="alert">
            {error}
            {!apiReady && (
              <button onClick={() => void refresh()}>Retry connection</button>
            )}
          </div>
        )}
        <form onSubmit={signIn}>
          <label htmlFor="login-identity">Username or email</label>
          <input
            id="login-identity"
            autoComplete="username"
            value={identity}
            onChange={(event) => setIdentity(event.target.value)}
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
          <button className="button primary wide" disabled={busy}>
            Sign in
          </button>
        </form>
        <span className="helper">
          Public registration is disabled. Contact an administrator for access.
        </span>
      </section>
    </main>
  ) : !user ? (
    <main className="login-screen">
      <div className="loading-card" role="status">
        Checking your saved session…
      </div>
    </main>
  ) : (
    <div className="app-frame">
      <aside className="sidebar">
        <Link href="/dashboard" className="brand">
          <span className="brand-mark">A</span>
          <span>AgentFlow</span>
        </Link>
        <span className="side-caption">WORKSPACE</span>
        <nav aria-label="Workspace navigation" className="side-nav">
          {nav.map(([label, href, icon]) => (
            <Link
              key={href}
              href={href}
              aria-current={section === href.slice(1) ? "page" : undefined}
            >
              <span className="nav-icon">{icon}</span>
              <span>{label}</span>
              {label === "Approvals" && pendingApprovalCount > 0 && (
                <b className="nav-count">{pendingApprovalCount}</b>
              )}
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="side-caption">DETERMINISTIC AUTOMATION</span>
          <span className="helper">
            Build, run, and inspect workflows. No AI setup required.
          </span>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumb">
            AgentFlow <span>/</span>{" "}
            {section === "workflows" && builderPath
              ? editingId
                ? "Edit workflow"
                : "New workflow"
              : section.charAt(0).toUpperCase() + section.slice(1)}
          </div>
          <div className="topbar-right">
            <span
              className={`connection ${apiReady === null ? "checking" : apiReady ? "online" : "offline"}`}
            >
              <i />
              {apiReady === null
                ? "Checking API…"
                : apiReady
                  ? "API connected"
                  : "API unavailable"}
            </span>
            <span className="user-chip">
              <span className="avatar">
                {user.username.slice(0, 2).toUpperCase()}
              </span>
              <span>
                <b>{user.username}</b>
                <small>{user.role}</small>
              </span>
            </span>
            <button
              className="button secondary"
              onClick={() => void signOut()}
              disabled={busy}
            >
              Sign out
            </button>
          </div>
        </header>
        <main className="page-content">
          {error && (
            <div
              className={`alert ${error === "Account created." ? "success-alert" : ""}`}
              role="alert"
            >
              <span>
                {error === "Account created."
                  ? "Done"
                  : "Something needs attention"}
              </span>
              <p>{error}</p>
              {apiReady ? (
                <button onClick={() => setError("")}>Dismiss</button>
              ) : (
                <button onClick={() => void refresh()}>Retry connection</button>
              )}
            </div>
          )}
          {((builderPath && user.role === "approver") ||
            ((section === "accounts" || section === "audit") &&
              user.role !== "admin")) && (
            <section className="panel permission-message" role="alert">
              <p className="eyebrow">ACCESS RESTRICTED</p>
              <h1>This page is not available for your role</h1>
              <p>
                Your account does not have permission to use this part of
                AgentFlow.
              </p>
              <Link className="button secondary" href="/dashboard">
                Return to dashboard
              </Link>
            </section>
          )}
          {section === "dashboard" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">WORKSPACE OVERVIEW</p>
                  <h1>Welcome, {user.username}</h1>
                  <p className="page-lede">
                    AgentFlow turns registered actions into repeatable
                    workflows, then saves each step so you can see exactly what
                    happened.
                  </p>
                </div>
                {user.role !== "approver" && (
                  <Link className="button primary" href="/workflows/new">
                    ＋ Create workflow
                  </Link>
                )}
              </div>
              {loading ? (
                <div className="empty">Loading your workspace…</div>
              ) : (
                <div className="metric-grid">
                  <article className="metric-card">
                    <strong>{workflows.length}</strong>
                    <span>Accessible workflows</span>
                    <Link href="/workflows">Browse workflows →</Link>
                  </article>
                  <article className="metric-card">
                    <strong>
                      {runs.filter((run) => run.status === "succeeded").length}
                    </strong>
                    <span>Recent successful runs</span>
                    <Link href="/runs">View run history →</Link>
                  </article>
                  <article className="metric-card">
                    <strong>
                      {runs.filter((run) => run.status === "failed").length}
                    </strong>
                    <span>Recent failed runs</span>
                    <Link href="/runs?status=failed">Inspect failures →</Link>
                  </article>
                  <article className="metric-card">
                    <strong>{pendingApprovalCount}</strong>
                    <span>Pending approvals visible to you</span>
                    <Link href="/approvals">Review requests →</Link>
                  </article>
                </div>
              )}
              {!tutorialDismissed && (
                <section className="panel getting-started">
                  <div>
                    <p className="eyebrow">FIRST WORKFLOW</p>
                    <h2>See AgentFlow in action</h2>
                    <p>
                      Try a real workflow that sets “hello agentflow”, converts
                      it to uppercase, and saves the result from the worker.
                    </p>
                    <div className="mini-flow">
                      <span>Starting text</span>
                      <b>→</b>
                      <span>Set a value</span>
                      <b>→</b>
                      <span>Uppercase</span>
                      <b>→</b>
                      <span>Saved output</span>
                    </div>
                  </div>
                  <div className="button-column">
                    <button
                      className="button primary"
                      onClick={() => void runTutorial()}
                      disabled={busy || !apiReady || user.role === "approver"}
                    >
                      Run Text Transformation
                    </button>
                    {user.role !== "approver" && (
                      <Link
                        href="/workflows/new?example=text"
                        className="button secondary"
                      >
                        Build my own workflow
                      </Link>
                    )}
                    <button
                      className="button secondary"
                      onClick={() => {
                        window.localStorage.setItem(
                          `agentflow-tutorial-dismissed-${user.id}`,
                          "true",
                        );
                        setTutorialDismissed(true);
                      }}
                    >
                      Dismiss getting started
                    </button>
                  </div>
                </section>
              )}
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">HOW IT WORKS</p>
                    <h2>Your workflow, one step at a time</h2>
                  </div>
                </div>
                <div className="concept-grid">
                  <div>
                    <b>1. Create</b>
                    <p>
                      Choose registered actions and configure their inputs in
                      the visual builder.
                    </p>
                  </div>
                  <div>
                    <b>2. Run</b>
                    <p>
                      The worker executes the saved version and records every
                      attempt.
                    </p>
                  </div>
                  <div>
                    <b>3. Inspect</b>
                    <p>
                      Review step outputs, failures, retries, approvals, and
                      audit events.
                    </p>
                  </div>
                </div>
              </section>
              <section className="panel">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">RECENT ACTIVITY</p>
                    <h2>Latest runs</h2>
                  </div>
                  <Link href="/runs">All runs →</Link>
                </div>
                {runs.length ? (
                  <RunList
                    runs={runs.slice(0, 6)}
                    workflows={workflows}
                    cancelRun={cancelRun}
                    busy={busy}
                  />
                ) : (
                  <div className="empty compact">
                    No runs yet. Create or try a workflow to see saved execution
                    results here.
                  </div>
                )}
              </section>
            </>
          )}
          {section === "workflows" && !builderPath && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">AUTOMATION LIBRARY</p>
                  <h1>Workflows</h1>
                  <p className="page-lede">
                    Reusable ordered sequences of registered actions.
                  </p>
                </div>
                {user.role !== "approver" && (
                  <Link className="button primary" href="/workflows/new">
                    ＋ Create workflow
                  </Link>
                )}
              </div>
              {examples.length > 0 && (
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">START WITH A TEMPLATE</p>
                      <h2>Runnable examples</h2>
                    </div>
                    <span className="helper">
                      Creates a workflow definition only; no sample runs or
                      history are inserted.
                    </span>
                  </div>
                  <div className="concept-grid">
                    {examples.map((template) => (
                      <article key={template.id}>
                        <b>{template.name}</b>
                        <p>{template.description}</p>
                        {user.role !== "approver" && (
                          <button
                            className="button secondary"
                            disabled={busy}
                            onClick={() => void createFromExample(template)}
                          >
                            Add to my workflows
                          </button>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              )}
              {workflowDetail && (
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">WORKFLOW DETAILS</p>
                      <h2>{workflowDetail.name}</h2>
                    </div>
                    <span className="version">
                      Version {workflowDetail.current_version_number}
                    </span>
                  </div>
                  <p>
                    {workflowDetail.description ||
                      "A reusable sequence of actions."}
                  </p>
                  <div className="step-preview">
                    {workflowDetail.definition.steps.map((step, index) => (
                      <div key={step.id} className="preview-step">
                        <b>{index + 1}</b>
                        <span>
                          <strong>
                            {step.type === "conditional"
                              ? "Conditional branch"
                              : displayAction(step.action, actions)}
                          </strong>
                          <small>
                            {step.type === "conditional"
                              ? "Condition · THEN / ELSE"
                              : step.action}
                          </small>
                        </span>
                      </div>
                    ))}
                  </div>
                  <details>
                    <summary>Advanced: view saved JSON</summary>
                    <pre>
                      {JSON.stringify(workflowDetail.definition, null, 2)}
                    </pre>
                  </details>
                  <div className="button-row">
                    <button
                      className="button primary"
                      onClick={() => void runWorkflow(workflowDetail.id)}
                      disabled={busy}
                    >
                      Run workflow
                    </button>
                    <button
                      className="button secondary"
                      onClick={() => startEdit(workflowDetail)}
                    >
                      Edit workflow
                    </button>
                    <Link
                      className="button secondary"
                      href={`/runs?workflow=${workflowDetail.id}`}
                    >
                      View run history
                    </Link>
                  </div>
                </section>
              )}
              {workflows.length ? (
                <div className="workflow-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Workflow</th>
                        <th>Steps</th>
                        <th>Current version</th>
                        <th>Updated</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {workflows.map((workflow) => (
                        <tr key={workflow.id}>
                          <td>
                            <Link
                              className="table-title"
                              href={`/workflows/${workflow.id}`}
                            >
                              {workflow.name}
                            </Link>
                            <small>
                              {workflow.description || "Reusable workflow"}
                            </small>
                          </td>
                          <td>{workflow.definition.steps?.length ?? 0}</td>
                          <td>v{workflow.current_version_number}</td>
                          <td>
                            {formatDate(
                              workflow.updated_at ?? workflow.created_at,
                            )}
                          </td>
                          <td>
                            <div className="table-actions">
                              <Link
                                className="button secondary small"
                                href={`/workflows/${workflow.id}`}
                              >
                                Open
                              </Link>
                              {user.role !== "approver" && (
                                <>
                                  <button
                                    className="button secondary small"
                                    onClick={() => startEdit(workflow)}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    className="button primary small"
                                    onClick={() =>
                                      void runWorkflow(workflow.id)
                                    }
                                    disabled={busy}
                                  >
                                    Run
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty">
                  <strong>Your workflow library is empty</strong>
                  <p>
                    Build a sequence of actions, save it, then run it whenever
                    you need the same process.
                  </p>
                  {user.role !== "approver" && (
                    <Link className="button primary" href="/workflows/new">
                      Create your first workflow
                    </Link>
                  )}
                </div>
              )}
            </>
          )}
          {builderPath && user.role !== "approver" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">VISUAL WORKFLOW BUILDER</p>
                  <h1>{editingId ? "Edit workflow" : "Create a workflow"}</h1>
                  <p className="page-lede">
                    Add actions in order and configure their inputs. Saving an
                    edit creates a new immutable version.
                  </p>
                </div>
                <Link
                  className="button secondary"
                  href="/workflows"
                  onClick={resetBuilder}
                >
                  Cancel
                </Link>
              </div>
              <form
                className="builder-layout"
                onSubmit={saveWorkflow}
                noValidate
              >
                <div className="builder-main">
                  {!editingId && (
                    <section className="panel proposal-panel">
                      <div className="section-heading">
                        <div>
                          <p className="eyebrow">OPTIONAL AI ASSIST</p>
                          <h2>Describe a workflow</h2>
                        </div>
                        <span className="helper">Preview only</span>
                      </div>
                      <p className="helper">
                        The configured AI gateway can draft registered actions
                        for you. Review and edit the proposal below; it is not
                        saved or run until you choose those actions.
                      </p>
                      <label htmlFor="proposal-model">AI model</label>
                      <select
                        id="proposal-model"
                        value={proposalModel}
                        onChange={(event) =>
                          setProposalModel(
                            event.target.value as ProposalModelId,
                          )
                        }
                        disabled={proposalBusy}
                      >
                        {proposalModels.map((model) => (
                          <option key={model.id} value={model.id}>
                            {model.label}
                          </option>
                        ))}
                      </select>
                      <p className="helper" aria-live="polite">
                        {
                          proposalModels.find(
                            (model) => model.id === proposalModel,
                          )?.description
                        }
                      </p>
                      <label htmlFor="workflow-prompt">
                        What should this workflow do?
                      </label>
                      <textarea
                        id="workflow-prompt"
                        rows={3}
                        maxLength={4000}
                        value={proposalPrompt}
                        onChange={(event) =>
                          setProposalPrompt(event.target.value)
                        }
                        placeholder="Convert customer names to uppercase and append a suffix."
                      />
                      <button
                        type="button"
                        className="button secondary"
                        onClick={() => void generateWorkflowProposal()}
                        disabled={proposalBusy || busy}
                      >
                        {proposalBusy
                          ? "Generating proposal…"
                          : "Generate proposal"}
                      </button>
                      {proposalError && (
                        <p className="validation-summary" role="alert">
                          {proposalError}
                        </p>
                      )}
                      {proposalReady && (
                        <p className="success-alert" role="status">
                          Proposal ready. Review the generated steps, edit as
                          needed, then explicitly save it. Running it is a
                          separate action after saving.
                        </p>
                      )}
                    </section>
                  )}
                  <section className="panel">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">BASIC DETAILS</p>
                        <h2>Name your workflow</h2>
                      </div>
                      <span className="version">Definition v1</span>
                    </div>
                    <label htmlFor="workflow-name">Workflow name</label>
                    <input
                      id="workflow-name"
                      value={workflowName}
                      onChange={(event) => setWorkflowName(event.target.value)}
                      maxLength={200}
                      placeholder="For example, Format a greeting"
                      required
                    />
                    <label htmlFor="workflow-description">
                      Description <span className="optional">optional</span>
                    </label>
                    <textarea
                      id="workflow-description"
                      rows={2}
                      maxLength={1000}
                      value={workflowDescription}
                      onChange={(event) =>
                        setWorkflowDescription(event.target.value)
                      }
                      placeholder="What should this workflow do?"
                    />
                  </section>
                  <section className="panel">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">RUNTIME DATA</p>
                        <h2>Workflow inputs</h2>
                      </div>
                      <button
                        type="button"
                        className="button secondary"
                        onClick={addWorkflowInput}
                      >
                        ＋ Add input
                      </button>
                    </div>
                    {Object.entries(workflowInputSchema).length === 0 ? (
                      <p className="helper">
                        Inputs make a saved workflow reusable with different
                        values each run.
                      </p>
                    ) : (
                      Object.entries(workflowInputSchema).map(
                        ([name, field]) => (
                          <div className="input-field" key={name}>
                            <div className="step-fields">
                              <label>
                                Input name
                                <input
                                  value={workflowInputNameDrafts[name] ?? name}
                                  onChange={(event) =>
                                    setWorkflowInputNameDrafts((drafts) => ({
                                      ...drafts,
                                      [name]: event.target.value,
                                    }))
                                  }
                                  onBlur={() => commitWorkflowInputName(name)}
                                />
                              </label>
                              <label>
                                Type
                                <select
                                  value={field.type}
                                  onChange={(event) =>
                                    updateWorkflowInput(name, name, {
                                      ...field,
                                      type: event.target.value,
                                    })
                                  }
                                >
                                  {[
                                    "string",
                                    "number",
                                    "integer",
                                    "boolean",
                                    "object",
                                    "array",
                                  ].map((type) => (
                                    <option key={type}>{type}</option>
                                  ))}
                                </select>
                              </label>
                              <label className="reference-choice">
                                <input
                                  type="checkbox"
                                  checked={field.required}
                                  onChange={(event) =>
                                    updateWorkflowInput(name, name, {
                                      ...field,
                                      required: event.target.checked,
                                    })
                                  }
                                />{" "}
                                Required
                              </label>
                              <button
                                type="button"
                                className="button secondary small"
                                onClick={() =>
                                  setWorkflowInputSchema((current) =>
                                    Object.fromEntries(
                                      Object.entries(current).filter(
                                        ([key]) => key !== name,
                                      ),
                                    ),
                                  )
                                }
                              >
                                Remove
                              </button>
                            </div>
                            <input
                              aria-label={`${name} description`}
                              placeholder="Input description (optional)"
                              value={field.description ?? ""}
                              onChange={(event) =>
                                updateWorkflowInput(name, name, {
                                  ...field,
                                  description: event.target.value,
                                })
                              }
                            />
                          </div>
                        ),
                      )
                    )}
                  </section>
                  <section className="panel builder-steps">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">ORDERED ACTIONS</p>
                        <h2>
                          Workflow steps{" "}
                          <span className="count">{builderSteps.length}</span>
                        </h2>
                      </div>
                      <button
                        className="button secondary"
                        type="button"
                        onClick={() => addStep()}
                        disabled={!actions.length}
                      >
                        ＋ Add step
                      </button>
                      <button
                        className="button secondary"
                        type="button"
                        onClick={addConditional}
                      >
                        ＋ Add condition
                      </button>
                    </div>
                    {!builderSteps.length ? (
                      <div className="empty builder-empty">
                        <strong>Start with an action</strong>
                        <p>
                          Choose a registered action to begin. Later steps can
                          use outputs from earlier steps.
                        </p>
                        <div className="action-picker">
                          {actions.map((action) => (
                            <button
                              type="button"
                              key={action.id}
                              className="action-choice"
                              onClick={() => addStep(action.id)}
                            >
                              <b>{displayAction(action.id, actions)}</b>
                              <span>{readableAction(action, action.id)}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="step-editor-list">
                        <div className="flow-end">Input</div>
                        {builderSteps.map((step, index) => {
                          const action = actions.find(
                            (item) => item.id === step.action,
                          );
                          const fields = Object.entries(
                            action?.input_schema.properties ?? {},
                          );
                          if (step.conditional) {
                            return (
                              <article className="step-editor" key={step.key}>
                                <div className="step-connector" />
                                <div className="step-editor-head">
                                  <span className="step-number">
                                    {index + 1}
                                  </span>
                                  <div className="step-title">
                                    <strong>Conditional branch</strong>
                                    <small>
                                      Only one path executes; the other is
                                      persisted as skipped.
                                    </small>
                                  </div>
                                  <button
                                    className="icon-button danger"
                                    aria-label={`Remove condition ${index + 1}`}
                                    type="button"
                                    onClick={() =>
                                      setBuilderSteps((steps) =>
                                        steps.filter((_, i) => i !== index),
                                      )
                                    }
                                  >
                                    ×
                                  </button>
                                </div>
                                <div className="step-fields">
                                  <label>
                                    Left value
                                    <input
                                      value={step.conditional.left}
                                      onChange={(event) =>
                                        changeCondition(
                                          index,
                                          "left",
                                          event.target.value,
                                        )
                                      }
                                      placeholder='"approved" or 10'
                                    />
                                  </label>
                                  <label>
                                    Comparison
                                    <select
                                      value={step.conditional.operator}
                                      onChange={(event) =>
                                        changeCondition(
                                          index,
                                          "operator",
                                          event.target.value,
                                        )
                                      }
                                    >
                                      {[
                                        "eq",
                                        "ne",
                                        "gt",
                                        "gte",
                                        "lt",
                                        "lte",
                                        "is_empty",
                                        "is_not_empty",
                                      ].map((operator) => (
                                        <option key={operator} value={operator}>
                                          {operator.replaceAll("_", " ")}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label>
                                    Right value
                                    <input
                                      value={step.conditional.right}
                                      onChange={(event) =>
                                        changeCondition(
                                          index,
                                          "right",
                                          event.target.value,
                                        )
                                      }
                                      placeholder='"approved" or 10'
                                    />
                                  </label>
                                </div>
                                <div className="step-fields">
                                  <label>
                                    When true
                                    <input
                                      value={step.conditional.thenValue}
                                      onChange={(event) =>
                                        changeCondition(
                                          index,
                                          "thenValue",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                  <label>
                                    When false
                                    <input
                                      value={step.conditional.elseValue}
                                      onChange={(event) =>
                                        changeCondition(
                                          index,
                                          "elseValue",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                </div>
                                <div className="step-meta">
                                  <span>
                                    THEN and ELSE each return a configured
                                    value.
                                  </span>
                                </div>
                              </article>
                            );
                          }
                          return (
                            <article className="step-editor" key={step.key}>
                              <div className="step-connector" />
                              <div className="step-editor-head">
                                <span className="step-number">{index + 1}</span>
                                <div className="step-title">
                                  <strong>
                                    {displayAction(step.action, actions)}
                                  </strong>
                                  <small>
                                    {readableAction(action, step.action)}
                                  </small>
                                </div>
                                <div className="step-controls">
                                  <button
                                    className="icon-button"
                                    aria-label={`Move step ${index + 1} up`}
                                    type="button"
                                    onClick={() => moveStep(index, -1)}
                                    disabled={index === 0}
                                  >
                                    ↑
                                  </button>
                                  <button
                                    className="icon-button"
                                    aria-label={`Move step ${index + 1} down`}
                                    type="button"
                                    onClick={() => moveStep(index, 1)}
                                    disabled={index === builderSteps.length - 1}
                                  >
                                    ↓
                                  </button>
                                  <button
                                    className="icon-button danger"
                                    aria-label={`Remove step ${index + 1}`}
                                    type="button"
                                    onClick={() =>
                                      setBuilderSteps((steps) =>
                                        steps.filter((_, i) => i !== index),
                                      )
                                    }
                                  >
                                    ×
                                  </button>
                                </div>
                              </div>
                              <label htmlFor={`step-action-${index}`}>
                                Action
                              </label>
                              <select
                                id={`step-action-${index}`}
                                value={step.action}
                                onChange={(event) =>
                                  changeAction(index, event.target.value)
                                }
                              >
                                {actions.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {displayAction(item.id, actions)} —{" "}
                                    {item.description}
                                  </option>
                                ))}
                              </select>
                              <div className="step-fields">
                                {fields.map(([field, schema]) => {
                                  const required =
                                    action?.input_schema.required?.includes(
                                      field,
                                    );
                                  const canReference =
                                    index > 0 ||
                                    Object.keys(workflowInputSchema).length > 0;
                                  return (
                                    <div key={field} className="input-field">
                                      <label htmlFor={`step-${index}-${field}`}>
                                        {field === "value" &&
                                        step.action === "append"
                                          ? "Starting value"
                                          : field.replaceAll("_", " ")}
                                        {required && (
                                          <span className="required"> *</span>
                                        )}
                                      </label>
                                      {canReference && (
                                        <select
                                          aria-label={`Reference source for ${field}`}
                                          value={step.refs[field] ?? ""}
                                          onChange={(event) => {
                                            changeRef(
                                              index,
                                              field,
                                              event.target.value,
                                            );
                                            changeRefPath(
                                              index,
                                              field,
                                              event.target.value.startsWith(
                                                "step:",
                                              )
                                                ? "/value"
                                                : "",
                                            );
                                          }}
                                        >
                                          <option value="">
                                            Use a literal value
                                          </option>
                                          {Object.keys(workflowInputSchema).map(
                                            (name) => (
                                              <option
                                                key={`input:${name}`}
                                                value={`input:${name}`}
                                              >
                                                Workflow input: {name}
                                              </option>
                                            ),
                                          )}
                                          {builderSteps
                                            .slice(0, index)
                                            .map((prior, priorIndex) => (
                                              <option
                                                key={`step:${prior.key}`}
                                                value={`step:${prior.key}`}
                                              >
                                                Step {priorIndex + 1}:{" "}
                                                {displayAction(
                                                  prior.action,
                                                  actions,
                                                )}
                                              </option>
                                            ))}
                                        </select>
                                      )}
                                      {step.refs[field] ? (
                                        <input
                                          aria-label={`Output path for ${field}`}
                                          value={step.paths[field] ?? ""}
                                          onChange={(event) =>
                                            changeRefPath(
                                              index,
                                              field,
                                              event.target.value,
                                            )
                                          }
                                          placeholder="JSON Pointer path, e.g. /value/email"
                                        />
                                      ) : schema.enum ? (
                                        <select
                                          id={`step-${index}-${field}`}
                                          value={step.inputs[field] ?? ""}
                                          onChange={(event) =>
                                            changeInput(
                                              index,
                                              field,
                                              event.target.value,
                                            )
                                          }
                                        >
                                          <option value="">Choose…</option>
                                          {schema.enum.map((item) => (
                                            <option
                                              key={String(item)}
                                              value={String(item)}
                                            >
                                              {String(item)}
                                            </option>
                                          ))}
                                        </select>
                                      ) : schema.type === "boolean" ? (
                                        <select
                                          id={`step-${index}-${field}`}
                                          value={step.inputs[field] ?? ""}
                                          onChange={(event) =>
                                            changeInput(
                                              index,
                                              field,
                                              event.target.value,
                                            )
                                          }
                                        >
                                          <option value="">Choose…</option>
                                          <option value="true">Yes</option>
                                          <option value="false">No</option>
                                        </select>
                                      ) : schema.type === "object" ||
                                        schema.type === "array" ||
                                        !schema.type ||
                                        schema.anyOf ? (
                                        <textarea
                                          id={`step-${index}-${field}`}
                                          rows={3}
                                          value={step.inputs[field] ?? ""}
                                          onChange={(event) =>
                                            changeInput(
                                              index,
                                              field,
                                              event.target.value,
                                            )
                                          }
                                          placeholder={
                                            schema.type === "array"
                                              ? "Enter JSON array, e.g. []"
                                              : schema.type === "object"
                                                ? "Enter JSON object, e.g. {}"
                                                : "Enter a JSON value"
                                          }
                                        />
                                      ) : (
                                        <input
                                          id={`step-${index}-${field}`}
                                          type={
                                            schema.type === "integer" ||
                                            schema.type === "number"
                                              ? "number"
                                              : "text"
                                          }
                                          min={schema.minimum}
                                          max={schema.maximum}
                                          maxLength={schema.maxLength}
                                          value={step.inputs[field] ?? ""}
                                          onChange={(event) =>
                                            changeInput(
                                              index,
                                              field,
                                              event.target.value,
                                            )
                                          }
                                          placeholder={
                                            field === "suffix"
                                              ? " text to add"
                                              : "Enter a value"
                                          }
                                        />
                                      )}
                                      {schema.description && (
                                        <small className="field-help">
                                          {schema.description}
                                        </small>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                              <div className="step-meta">
                                {action?.approval_required ? (
                                  <span className="attention">
                                    Approval required before execution
                                  </span>
                                ) : (
                                  <span>
                                    {action?.category ?? "Configured action"} ·{" "}
                                    {action?.timeout_seconds ?? "—"}s timeout ·{" "}
                                    {action?.max_attempts ?? "—"} attempt(s)
                                  </span>
                                )}
                              </div>
                            </article>
                          );
                        })}
                        <div className="flow-end">Workflow output</div>
                        <button
                          type="button"
                          className="button add-step-inline"
                          onClick={() => addStep()}
                        >
                          ＋ Add another step
                        </button>
                      </div>
                    )}
                  </section>
                  {validation && (
                    <div className="validation-summary" role="alert">
                      <b>Check the workflow</b>
                      <span>{validation}</span>
                    </div>
                  )}
                  <div className="save-row">
                    <span>
                      Inputs and outputs are validated again by the server.
                    </span>
                    <div className="button-row">
                      {editingId && (
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => {
                            resetBuilder();
                            router.push("/workflows");
                          }}
                        >
                          Discard changes
                        </button>
                      )}
                      <button
                        className="button primary"
                        disabled={busy || !apiReady || loadingEdit}
                      >
                        {editingId ? "Save new version" : "Save workflow"}
                      </button>
                    </div>
                  </div>
                </div>
                <aside className="builder-aside">
                  <section className="panel preview-panel">
                    <p className="eyebrow">LIVE PREVIEW</p>
                    <h2>{workflowName || "Untitled workflow"}</h2>
                    <p className="helper">
                      The order shown here is the order the worker follows.
                    </p>
                    <div className="workflow-preview">
                      <div className="preview-node start-node">
                        Starting input
                      </div>
                      {builderSteps.map((step, index) => (
                        <div key={step.key} className="preview-item">
                          <span className="preview-line" />
                          <div className="preview-node">
                            <b>{index + 1}</b>
                            <span>
                              <strong>
                                {step.conditional
                                  ? "Conditional branch"
                                  : displayAction(step.action, actions)}
                              </strong>
                              <small>
                                {step.conditional
                                  ? `${step.conditional.left || "left"} ${step.conditional.operator} ${step.conditional.right || "right"}`
                                  : step.refs.value
                                    ? step.refs.value.startsWith("step:")
                                      ? `Uses output from step ${builderSteps.findIndex((item) => item.key === step.refs.value.slice(5)) + 1}`
                                      : `Uses workflow input ${step.refs.value.slice(6)}`
                                    : readableAction(
                                        actions.find(
                                          (item) => item.id === step.action,
                                        ),
                                        step.action,
                                      )}
                              </small>
                              {step.conditional && (
                                <small>
                                  THEN →{" "}
                                  {step.conditional.thenValue || "result"} ·
                                  ELSE →{" "}
                                  {step.conditional.elseValue || "result"}
                                </small>
                              )}
                            </span>
                          </div>
                        </div>
                      ))}
                      <div className="preview-item">
                        <span className="preview-line" />
                        <div className="preview-node output-node">
                          Saved output
                        </div>
                      </div>
                    </div>
                    <details className="json-details">
                      <summary>Advanced: view JSON</summary>
                      <pre>{JSON.stringify(generatedDefinition, null, 2)}</pre>
                    </details>
                  </section>
                  <section className="panel action-help">
                    <p className="eyebrow">REGISTERED ACTIONS</p>
                    <h2>Available actions</h2>
                    {actions.map((action) => (
                      <div key={action.id}>
                        <b>{displayAction(action.id, actions)}</b>
                        <p>{action.description}</p>
                        <small className="field-help">
                          {action.category} · {action.timeout_seconds}s timeout
                          · {action.max_attempts} attempt(s)
                          {action.approval_required
                            ? " · approval required"
                            : ""}
                        </small>
                        <small className="field-help">
                          Output:{" "}
                          {Object.keys(
                            action.output_schema?.properties ?? {},
                          ).join(", ") || "structured JSON value"}
                        </small>
                      </div>
                    ))}
                  </section>
                </aside>
              </form>
            </>
          )}
          {section === "runs" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">PERSISTED EXECUTIONS</p>
                  <h1>{routeId ? `Run #${routeId}` : "Run history"}</h1>
                  <p className="page-lede">
                    Every run is pinned to the saved workflow version it
                    executed.
                  </p>
                </div>
                {routeId && (
                  <Link className="button secondary" href="/runs">
                    ← All runs
                  </Link>
                )}
              </div>
              {routeId ? (
                runDetail ? (
                  <RunDetail
                    run={runDetail}
                    workflow={workflows.find(
                      (item) => item.id === runDetail.workflow_id,
                    )}
                    cancelRun={cancelRun}
                    busy={busy}
                  />
                ) : (
                  <div className="empty">
                    Run not found in your recent history. Refresh or return to
                    all runs.
                  </div>
                )
              ) : runs.length ? (
                <RunList
                  runs={runs}
                  workflows={workflows}
                  cancelRun={cancelRun}
                  busy={busy}
                />
              ) : (
                <div className="empty">
                  <strong>No runs yet</strong>
                  <p>
                    When you run a saved workflow, its status, timing, steps,
                    outputs, and attempts appear here.
                  </p>
                  <Link href="/workflows">Browse workflows</Link>
                </div>
              )}
            </>
          )}
          {section === "approvals" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">HUMAN REVIEW</p>
                  <h1>Approvals</h1>
                  <p className="page-lede">
                    A protected action stays blocked until an authorized person
                    records a decision.
                  </p>
                </div>
              </div>
              {approvals.length ? (
                <div className="approval-list">
                  {approvals.map((item) => (
                    <article className="panel approval-card" key={item.id}>
                      <div className="section-heading">
                        <div>
                          <span className={`run-state ${item.status}`}>
                            {item.status.replaceAll("_", " ")}
                          </span>
                          <h2>
                            {displayAction(item.action, actions)}{" "}
                            <small>in run #{item.run_id}</small>
                          </h2>
                        </div>
                        <time>{formatDate(item.created_at)}</time>
                      </div>
                      <p>
                        {item.status === "pending"
                          ? "This action is waiting for a human decision before the worker can continue."
                          : `This request was ${item.status}.`}
                      </p>
                      <div className="approval-context">
                        <span>
                          <small>Workflow</small>
                          <b>
                            {workflows.find((wf) => wf.id === item.workflow_id)
                              ?.name ?? `Workflow #${item.workflow_id ?? "—"}`}
                          </b>
                        </span>
                        <span>
                          <small>Step</small>
                          <b>{item.step_id ?? "—"}</b>
                        </span>
                        <span>
                          <small>Requested by</small>
                          <b>User #{item.requester_id ?? "—"}</b>
                        </span>
                      </div>
                      <details>
                        <summary>Review safe action inputs</summary>
                        <pre>{JSON.stringify(item.inputs, null, 2)}</pre>
                      </details>
                      {item.reason && (
                        <p className="decision-note">
                          Decision note: {item.reason}
                        </p>
                      )}
                      {item.status === "pending" &&
                        user &&
                        ["admin", "approver"].includes(user.role) &&
                        item.requester_id !== user.id && (
                          <div className="decision-controls">
                            <label htmlFor={`decision-${item.id}`}>
                              Decision reason{" "}
                              <span className="optional">optional</span>
                            </label>
                            <input
                              id={`decision-${item.id}`}
                              maxLength={1000}
                              value={approvalReasons[item.id] ?? ""}
                              onChange={(event) =>
                                setApprovalReasons((current) => ({
                                  ...current,
                                  [item.id]: event.target.value,
                                }))
                              }
                              placeholder="Explain your decision"
                            />
                            <button
                              className="button primary"
                              disabled={busy}
                              onClick={() =>
                                void decideApproval(item.id, "approved")
                              }
                            >
                              Approve
                            </button>
                            <button
                              className="button danger-button"
                              disabled={busy}
                              onClick={() =>
                                void decideApproval(item.id, "rejected")
                              }
                            >
                              Reject
                            </button>
                          </div>
                        )}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="empty">
                  <strong>No approval requests</strong>
                  <p>
                    Requests visible to your role will appear here with their
                    action, workflow, and safe input context.
                  </p>
                </div>
              )}
            </>
          )}
          {section === "audit" && user.role === "admin" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">SECURITY HISTORY</p>
                  <h1>Audit history</h1>
                  <p className="page-lede">
                    Structured records of important account, workflow, run, and
                    approval events.
                  </p>
                </div>
              </div>
              {events.length ? (
                <div className="workflow-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Event</th>
                        <th>Resource</th>
                        <th>Outcome</th>
                        <th>Actor</th>
                        <th>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody>
                      {events.map((event) => (
                        <tr key={event.id}>
                          <td>
                            <b>{event.event_type}</b>
                          </td>
                          <td>
                            {event.resource_type}
                            {event.resource_id ? ` #${event.resource_id}` : ""}
                          </td>
                          <td>
                            <span className={`run-state ${event.outcome}`}>
                              {event.outcome}
                            </span>
                          </td>
                          <td>
                            {event.actor_id
                              ? `User #${event.actor_id}`
                              : "System"}
                          </td>
                          <td>{formatDate(event.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {canLoadMoreAudit && (
                    <button
                      className="button secondary load-more"
                      onClick={() => void loadOlderAudit()}
                      disabled={busy}
                    >
                      Load older events
                    </button>
                  )}
                </div>
              ) : (
                <div className="empty">No audit events recorded yet.</div>
              )}
            </>
          )}
          {section === "accounts" && user.role === "admin" && (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">ADMINISTRATION</p>
                  <h1>Accounts</h1>
                  <p className="page-lede">
                    Create sign-in accounts and assign the minimum role needed
                    for each person.
                  </p>
                </div>
              </div>
              <div className="admin-layout">
                <form className="panel account-form" onSubmit={createAccount}>
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">NEW ACCOUNT</p>
                      <h2>Create a user</h2>
                    </div>
                  </div>
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
                    minLength={8}
                    maxLength={8}
                    pattern="(?=.*[0-9])(?=.*[^A-Za-z0-9\\s]).{8}"
                    title="Use exactly 8 characters, including at least one number and one symbol."
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
                  <button className="button primary" disabled={busy}>
                    Create account
                  </button>
                  <p className="helper">
                    Use exactly 8 characters, including a number and a symbol.
                    Passwords are hashed and never returned by the API.
                  </p>
                </form>
                <section className="panel role-guide">
                  <p className="eyebrow">ROLE GUIDE</p>
                  <h2>Choose a role</h2>
                  <div>
                    <b>Administrator</b>
                    <p>
                      Manage accounts and access all workflows, runs, approvals,
                      and audit history.
                    </p>
                  </div>
                  <div>
                    <b>Operator</b>
                    <p>
                      Create and manage owned workflows and runs. Operators
                      cannot decide approvals or see admin audit records.
                    </p>
                  </div>
                  <div>
                    <b>Approver</b>
                    <p>
                      Review eligible pending requests. Approvers cannot edit
                      workflows, start runs, or approve their own requests.
                    </p>
                  </div>
                </section>
              </div>
            </>
          )}
          {runInputWorkflow && (
            <div className="modal-backdrop">
              <section
                className="panel run-input-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="run-input-title"
              >
                <p className="eyebrow">WORKFLOW INPUT</p>
                <h2 id="run-input-title">Run {runInputWorkflow.name}</h2>
                <p className="helper">
                  Provide the values declared by this workflow before it starts.
                </p>
                <form onSubmit={submitRunInput}>
                  {Object.entries(
                    runInputWorkflow.definition.input_schema ?? {},
                  ).map(([name, field]) => (
                    <div className="input-field" key={name}>
                      <label htmlFor={`run-input-${name}`}>
                        {name.replaceAll("_", " ")}
                        {field.required && <span className="required"> *</span>}
                      </label>
                      {field.type === "boolean" ? (
                        <select
                          id={`run-input-${name}`}
                          value={runInputValues[name] ?? "false"}
                          onChange={(event) =>
                            setRunInputValues((values) => ({
                              ...values,
                              [name]: event.target.value,
                            }))
                          }
                        >
                          <option value="true">Yes</option>
                          <option value="false">No</option>
                        </select>
                      ) : field.type === "object" || field.type === "array" ? (
                        <textarea
                          id={`run-input-${name}`}
                          rows={4}
                          value={runInputValues[name] ?? ""}
                          onChange={(event) =>
                            setRunInputValues((values) => ({
                              ...values,
                              [name]: event.target.value,
                            }))
                          }
                          placeholder={field.type === "array" ? "[]" : "{}"}
                        />
                      ) : (
                        <input
                          id={`run-input-${name}`}
                          type={
                            field.type === "number" || field.type === "integer"
                              ? "number"
                              : "text"
                          }
                          value={runInputValues[name] ?? ""}
                          onChange={(event) =>
                            setRunInputValues((values) => ({
                              ...values,
                              [name]: event.target.value,
                            }))
                          }
                        />
                      )}
                      {field.description && (
                        <small className="field-help">
                          {field.description}
                        </small>
                      )}
                    </div>
                  ))}
                  <div className="button-row">
                    <button
                      className="button secondary"
                      type="button"
                      onClick={() => setRunInputWorkflow(null)}
                    >
                      Cancel
                    </button>
                    <button className="button primary" disabled={busy}>
                      Start workflow
                    </button>
                  </div>
                </form>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function RunList({
  runs,
  workflows,
  cancelRun,
  busy,
}: {
  runs: Run[];
  workflows: Workflow[];
  cancelRun: (id: number) => void;
  busy: boolean;
}) {
  return (
    <div className="run-list">
      {runs.map((run) => (
        <article className="run-card" key={run.id}>
          <div className="run-title">
            <Link href={`/runs/${run.id}`} className="table-title">
              Run #{run.id}
            </Link>
            <span className={`run-state ${run.status}`}>
              {run.status.replaceAll("_", " ")}
            </span>
            <span className="run-time">{formatDate(run.created_at)}</span>
            {activeStatuses.has(run.status) &&
              run.status !== "cancel_requested" && (
                <button
                  className="button secondary small"
                  onClick={() => cancelRun(run.id)}
                  disabled={busy}
                >
                  Cancel
                </button>
              )}
          </div>
          <p className="run-summary">
            {workflows.find((item) => item.id === run.workflow_id)?.name ??
              `Workflow #${run.workflow_id}`}{" "}
            · version {run.workflow_version_number} ·{" "}
            {run.summary.succeeded_steps} succeeded, {run.summary.failed_steps}{" "}
            failed, {run.summary.not_started_steps} not started ·{" "}
            {run.summary.total_steps} steps
          </p>
          {run.error && (
            <p className="run-error">
              {run.error_category ? `${run.error_category}: ` : ""}
              {run.error}
            </p>
          )}
          <Link href={`/runs/${run.id}`} className="detail-link">
            Inspect steps and attempts →
          </Link>
        </article>
      ))}
    </div>
  );
}

function RunDetail({
  run,
  workflow,
  cancelRun,
  busy,
}: {
  run: Run;
  workflow?: Workflow;
  cancelRun: (id: number) => void;
  busy: boolean;
}) {
  return (
    <section className="panel run-detail">
      <div className="section-heading">
        <div>
          <span className={`run-state ${run.status}`}>
            {run.status.replaceAll("_", " ")}
          </span>
          <h2>
            {workflow?.name ?? `Workflow #${run.workflow_id}`}{" "}
            <small>version {run.workflow_version_number}</small>
          </h2>
        </div>
        {activeStatuses.has(run.status) &&
          run.status !== "cancel_requested" && (
            <button
              className="button secondary"
              onClick={() => cancelRun(run.id)}
              disabled={busy}
            >
              Cancel run
            </button>
          )}
      </div>
      <div className="run-facts">
        <span>
          Run identifier <b>#{run.id}</b>
        </span>
        <span>
          Created <b>{formatDate(run.created_at)}</b>
        </span>
        <span>
          Started <b>{formatDate(run.started_at)}</b>
        </span>
        <span>
          Finished <b>{formatDate(run.finished_at)}</b>
        </span>
        <span>
          Steps{" "}
          <b>
            {run.summary.succeeded_steps} succeeded · {run.summary.failed_steps}{" "}
            failed · {run.summary.not_started_steps} not started
          </b>
        </span>
        <span>
          Duration <b>{duration(run.started_at, run.finished_at)}</b>
        </span>
      </div>
      {run.error && (
        <div className="failure-summary">
          <b>{run.error_category ?? "Run failed"}</b>
          <p>{run.error}</p>
        </div>
      )}
      {run.output && (
        <details className="workflow-output" open>
          <summary>Workflow output</summary>
          <pre>{pretty(run.output)}</pre>
        </details>
      )}
      <div className="timeline">
        {run.steps.length ? (
          run.steps.map((step, index) => (
            <article className="timeline-step" key={step.id}>
              <span className={`timeline-mark ${step.status}`}>
                {step.status === "succeeded" ? "✓" : index + 1}
              </span>
              <div className="timeline-content">
                <div className="section-heading">
                  <div>
                    <h3>
                      {step.action === "compare_values" &&
                      typeof step.output?.decision === "boolean"
                        ? "Conditional decision"
                        : step.action.replaceAll("_", " ")}
                    </h3>
                    <p>
                      Step {index + 1} · {step.id} ·{" "}
                      <span className={`run-state ${step.status}`}>
                        {step.status}
                      </span>
                      {typeof step.output?.decision === "boolean" &&
                        ` · ${step.output.decision ? "THEN branch" : "ELSE branch"}`}
                    </p>
                  </div>
                </div>
                <div className="io-grid">
                  <div>
                    <small>Input</small>
                    <pre>{pretty(step.inputs ?? {})}</pre>
                  </div>
                  <div>
                    <small>Output</small>
                    <pre>
                      {pretty(step.output ?? step.error ?? step.status)}
                    </pre>
                  </div>
                </div>
                {step.error && (
                  <p className="run-error">
                    {step.error_category ? `${step.error_category}: ` : ""}
                    {step.error}
                  </p>
                )}
                {step.attempts.length > 0 && (
                  <details className="attempt-details" open>
                    <summary>Attempt history ({step.attempts.length})</summary>
                    <div>
                      {step.attempts.map((attempt) => (
                        <p key={attempt.attempt_number}>
                          <b>Attempt {attempt.attempt_number}</b> ·{" "}
                          {attempt.status}
                          {attempt.error_category
                            ? ` · ${attempt.error_category}`
                            : ""}
                          {attempt.error ? ` · ${attempt.error}` : ""}
                          <span>
                            {formatDate(attempt.started_at)}
                            {attempt.finished_at
                              ? ` — ${formatDate(attempt.finished_at)}`
                              : ""}
                          </span>
                        </p>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </article>
          ))
        ) : (
          <div className="empty compact">
            Step records will appear as the worker progresses.
          </div>
        )}
      </div>
    </section>
  );
}
