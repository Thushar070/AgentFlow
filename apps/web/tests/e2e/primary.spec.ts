import { expect, test, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";

const password = process.env.E2E_PASSWORD ?? "E2e1!pwX";
let worker: ChildProcess | undefined;

test.afterAll(() => {
  if (worker && worker.exitCode === null) worker.kill("SIGTERM");
});

async function signIn(page: Page, username: string, pass = password) {
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username or email").fill(username);
  await page.getByLabel("Password").fill(pass);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("heading", { name: `Welcome, ${username}` }),
  ).toBeVisible();
  await expect(page.getByText("API connected")).toBeVisible();
}

async function createTextWorkflow(page: Page, name: string) {
  await page.goto("/workflows/new");
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page.locator(".validation-summary").last()).toContainText(
    "Enter a workflow name.",
  );
  await page.getByLabel("Workflow name").fill(name);
  await page.getByRole("button", { name: "Set a value" }).click();
  await page.locator("#step-0-value").fill("hello agentflow");
  await page.getByRole("button", { name: "Add another step" }).click();
  await page.locator("#step-action-1").selectOption("uppercase");
  await page
    .getByLabel("Reference source for value")
    .selectOption("step:step_1");
  await page.getByLabel("Output path for value").fill("/value");
  await expect(
    page.locator(".preview-item .preview-node").nth(1),
  ).toContainText("Uses output from step 1");
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page).toHaveURL(/\/workflows$/);
  await expect(page.getByRole("link", { name })).toBeVisible();
}

async function createAccount(
  page: Page,
  username: string,
  role: string,
  email: string,
) {
  await page.goto("/accounts");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Initial password").fill("User1!pw");
  await page.getByLabel("Role", { exact: true }).selectOption(role);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.locator(".success-alert")).toContainText(
    "Account created.",
  );
}

async function createAndRunExample(
  page: Page,
  name: string,
  inputs: Record<string, string>,
) {
  await page.goto("/workflows");
  const card = page.locator(".concept-grid article").filter({ hasText: name });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Add to my workflows" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("button", { name: "Run workflow" }).click();
  for (const [field, value] of Object.entries(inputs)) {
    await page.locator(`#run-input-${field}`).fill(value);
  }
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  return Number(page.url().match(/runs\/(\d+)/)?.[1]);
}

test("visual workflow journey, persisted run details, roles, approvals, audit, and responsive navigation", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Create workflow" })).toHaveCount(
    0,
  );
  const denied = await page.request.get(
    `${process.env.E2E_API_URL}/api/workflows`,
  );
  expect(denied.status()).toBe(401);
  await signIn(page, "phase6admin");

  // A lost API connection leaves the app recoverable and preserves the browser session.
  await page.route(`${process.env.E2E_API_URL}/**`, (route) => route.abort());
  await page.reload();
  await expect(page.getByText("API unavailable")).toBeVisible();
  await page.unrouteAll();
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome, phase6admin" }),
  ).toBeVisible();

  await createTextWorkflow(page, "Cancel before worker");
  await page
    .getByRole("row")
    .filter({ hasText: "Cancel before worker" })
    .getByRole("button", { name: "Run" })
    .click();
  await expect(page.locator(".run-state").first()).toHaveText("queued");
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.locator(".run-state").first()).toHaveText("cancelled");

  worker = spawn(process.env.E2E_PYTHON!, ["-m", "agentflow.worker"], {
    cwd: process.env.E2E_ROOT,
    env: process.env,
    stdio: "ignore",
  });
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 700));
  expect(worker.exitCode).toBeNull();

  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Run Text Transformation" }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".io-grid").last()).toContainText(
    "HELLO AGENTFLOW",
  );
  await page.goto("/dashboard");
  await page.reload();
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.screenshot({
    path: "/tmp/agentflow-ux-1920x1080.png",
    fullPage: false,
  });
  const tutorialCount = await page.request.get(
    `${process.env.E2E_API_URL}/api/workflows`,
  );
  const tutorialWorkflows = (await tutorialCount.json()) as { name: string }[];
  expect(
    tutorialWorkflows.filter((item) => item.name === "Text Transformation"),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "Dismiss getting started" }).click();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "See AgentFlow in action" }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 900 });

  await createTextWorkflow(page, "Visual text workflow");
  const workflowRow = page
    .getByRole("row")
    .filter({ hasText: "Visual text workflow" });
  await expect(workflowRow).toContainText("v1");
  await workflowRow.getByRole("button", { name: "Edit" }).click();
  await expect(
    page.getByRole("heading", { name: "Edit workflow" }),
  ).toBeVisible();
  await page
    .locator("#workflow-description")
    .fill("Second immutable version created from visual forms.");
  await page.getByRole("button", { name: "Save new version" }).click();
  await expect(
    page.getByRole("row").filter({ hasText: "Visual text workflow" }),
  ).toContainText("v2");

  await page
    .getByRole("row")
    .filter({ hasText: "Visual text workflow" })
    .getByRole("button", { name: "Run" })
    .click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".io-grid").last()).toContainText(
    "HELLO AGENTFLOW",
  );
  await expect(page.locator(".timeline-step")).toHaveCount(2);
  await page.screenshot({
    path: "/tmp/agentflow-ux-1440x900.png",
    fullPage: false,
  });
  await page.reload();
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".io-grid").last()).toContainText(
    "HELLO AGENTFLOW",
  );

  await page.goto("/workflows/new");
  await page.getByLabel("Workflow name").fill("Conditional visual workflow");
  await page.getByRole("button", { name: "Add condition" }).click();
  await page.getByLabel("Left value").fill("approved");
  await page.getByLabel("Right value").fill("approved");
  await page.getByLabel("When true").fill("accepted");
  await page.getByLabel("When false").fill("rejected");
  await page.getByRole("button", { name: "Save workflow" }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "Conditional visual workflow" })
    .getByRole("button", { name: "Run" })
    .click();
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".workflow-output")).toContainText("accepted");
  await expect(page.locator(".timeline-step")).toHaveCount(3);

  // Server validation remains authoritative even though the visual form prevents this invalid definition.
  const authResponse = await page.request.get(
    `${process.env.E2E_API_URL}/api/auth/me`,
  );
  const auth = (await authResponse.json()) as { csrf_token: string };
  const invalidWorkflow = await page.request.post(
    `${process.env.E2E_API_URL}/api/workflows`,
    {
      data: {
        version: 1,
        name: "Invalid action",
        steps: [{ id: "bad", action: "not_registered", inputs: {} }],
      },
      headers: {
        Origin: process.env.E2E_WEB_URL!,
        "X-CSRF-Token": auth.csrf_token,
      },
    },
  );
  expect(invalidWorkflow.status()).toBe(422);
  const runtimeFailureWorkflow = await page.request.post(
    `${process.env.E2E_API_URL}/api/workflows`,
    {
      data: {
        version: 1,
        name: "Safe runtime failure",
        steps: [
          { id: "bad_json", action: "parse_json", inputs: { value: "{bad" } },
        ],
      },
      headers: {
        Origin: process.env.E2E_WEB_URL!,
        "X-CSRF-Token": auth.csrf_token,
      },
    },
  );
  expect(runtimeFailureWorkflow.status()).toBe(201);
  const runtimeFailure = (await runtimeFailureWorkflow.json()) as {
    id: number;
  };
  await page.goto("/workflows");
  await page
    .getByRole("row")
    .filter({ hasText: "Safe runtime failure" })
    .getByRole("button", { name: "Run" })
    .click();
  await expect(page.locator(".run-state").first()).toHaveText("failed");
  await expect(page.locator(".run-detail")).toContainText("invalid_json");
  await expect(page.locator(".run-detail")).not.toContainText("Traceback");
  expect(runtimeFailure.id).toBeGreaterThan(0);
  await expect(page.getByRole("link", { name: "Audit history" })).toBeVisible();
  await page.goto("/audit");
  await expect(
    page.getByRole("heading", { name: "Audit history" }),
  ).toBeVisible();
  await expect(page.locator(".data-table")).toContainText("workflow");

  await createAccount(
    page,
    "phase6operator",
    "operator",
    "phase6operator@example.test",
  );
  await createAccount(
    page,
    "phase6approver",
    "approver",
    "phase6approver@example.test",
  );

  // The API-backed templates are runnable definitions and must not seed sample execution history.
  const customerRun = await createAndRunExample(page, "Customer validation", {
    customer: JSON.stringify({ name: "Ada", email: "ada@example.test" }),
  });
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".workflow-output")).toContainText("valid");
  await expect(page.locator(".timeline-step")).toHaveCount(6);
  await page.reload();
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  expect(customerRun).toBeGreaterThan(0);

  await createAndRunExample(page, "API data processing", {
    api_url: "http://127.0.0.1:18002/data",
  });
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".workflow-output")).toContainText("5");
  await createAndRunExample(page, "Data cleaning pipeline", {
    records: JSON.stringify([
      {
        active: true,
        name: " Kept ",
        email: " keep@example.test ",
        amount: 2,
      },
      {
        active: false,
        name: "Removed",
        email: "remove@example.test",
        amount: 3,
      },
      { active: true, name: "Missing fields", amount: 7 },
    ]),
  });
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".workflow-output")).toContainText("Kept");

  await page.goto("/workflows/new");
  await page
    .getByLabel("What should this workflow do?")
    .fill("Convert customer names to uppercase and append a suffix.");
  await expect(page.getByLabel("AI model")).toHaveValue("freellmapi");
  await expect(
    page.getByText(/FreeLLMAPI unified key and its active fallback chain/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(page.getByRole("status")).toContainText("Proposal ready");
  await expect(page.locator("#workflow-name")).toHaveValue(
    "Generated text workflow",
  );
  await expect(page.locator(".step-editor")).toHaveCount(3);
  await page.locator("#workflow-name").fill("AI edited customer name");
  await page.getByLabel("Reference source for suffix").selectOption("");
  await page.locator("#step-2-suffix").fill("!");
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page).toHaveURL(/\/workflows$/);
  const generatedRow = page
    .getByRole("row")
    .filter({ hasText: "AI edited customer name" });
  await expect(generatedRow).toBeVisible();
  await generatedRow.getByRole("button", { name: "Run" }).click();
  await page.locator("#run-input-customer_name").fill("Ada Lovelace");
  await page.locator("#run-input-suffix").fill("!");
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(page).toHaveURL(/\/runs\/\d+$/);
  const generatedRunId = Number(page.url().match(/runs\/(\d+)/)?.[1]);
  await expect(page.locator(".run-state").first()).toHaveText(
    "awaiting approval",
  );
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6approver", "User1!pw");
  await page.goto("/approvals");
  const generatedApproval = page
    .locator(".approval-card")
    .filter({ hasText: `run #${generatedRunId}` })
    .first();
  await expect(generatedApproval).toBeVisible();
  await generatedApproval.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6admin");
  await page.goto(`/runs/${generatedRunId}`);
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".workflow-output")).toContainText("ADA LOVELACE!");

  const beforeInvalidProposal = await Promise.all([
    page.request.get(`${process.env.E2E_API_URL}/api/workflows`),
    page.request.get(`${process.env.E2E_API_URL}/api/runs?limit=100`),
  ]);
  const workflowCountBefore = (
    (await beforeInvalidProposal[0].json()) as unknown[]
  ).length;
  const runCountBefore = ((await beforeInvalidProposal[1].json()) as unknown[])
    .length;
  await page.goto("/workflows/new");
  await page.getByLabel("What should this workflow do?").fill("invalid action");
  await page.getByRole("button", { name: "Generate proposal" }).click();
  await expect(page.locator(".validation-summary")).toContainText(
    "not registered",
  );
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page.locator(".validation-summary").last()).toContainText(
    "Enter a workflow name.",
  );
  const afterInvalidProposal = await Promise.all([
    page.request.get(`${process.env.E2E_API_URL}/api/workflows`),
    page.request.get(`${process.env.E2E_API_URL}/api/runs?limit=100`),
  ]);
  expect(((await afterInvalidProposal[0].json()) as unknown[]).length).toBe(
    workflowCountBefore,
  );
  expect(((await afterInvalidProposal[1].json()) as unknown[]).length).toBe(
    runCountBefore,
  );

  const approvalExampleRun = await createAndRunExample(page, "Human approval", {
    request: JSON.stringify({ operation: "delete", resource: "sample" }),
  });
  await expect(page.locator(".run-state").first()).toHaveText(
    "awaiting approval",
  );
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6approver", "User1!pw");
  await page.goto("/approvals");
  const templateApproval = page
    .locator(".approval-card")
    .filter({ hasText: `run #${approvalExampleRun}` })
    .first();
  await expect(templateApproval).toBeVisible();
  await templateApproval.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6admin");
  await page.goto(`/runs/${approvalExampleRun}`);
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6operator", "User1!pw");
  await expect(page.getByRole("link", { name: "Audit history" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("link", { name: "Accounts" })).toHaveCount(0);
  await page.goto("/accounts");
  await expect(page.locator(".permission-message")).toContainText(
    "ACCESS RESTRICTED",
  );
  const crossResource = await page.request.get(
    `${process.env.E2E_API_URL}/api/workflows/1`,
  );
  expect([403, 404]).toContain(crossResource.status());
  const deniedAudit = await page.request.get(
    `${process.env.E2E_API_URL}/api/audit?limit=1`,
  );
  expect(deniedAudit.status()).toBe(403);
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6admin");

  // append is configured as approval-required in the isolated E2E server; the worker blocks until a decision.
  await page.goto("/workflows/new");
  await page.getByLabel("Workflow name").fill("Approval acceptance");
  await page.getByRole("button", { name: "Append text" }).click();
  await page.locator("#step-0-value").fill("request");
  await page.locator("#step-0-suffix").fill("-approved");
  await page.getByRole("button", { name: "Save workflow" }).click();
  const approvalRow = page
    .getByRole("row")
    .filter({ hasText: "Approval acceptance" });
  await approvalRow.getByRole("button", { name: "Run" }).click();
  await expect(page.locator(".run-state").first()).toHaveText(
    "awaiting approval",
  );
  const pendingApproval = page.locator(".nav-count");
  await expect(pendingApproval).toHaveText("1");
  const approvalResponse = await page.request.get(
    `${process.env.E2E_API_URL}/api/approvals?limit=100`,
  );
  const approvalItems = (await approvalResponse.json()) as {
    id: number;
    run_id: number;
  }[];
  const approval =
    approvalItems.find(
      (item) => item.run_id === Number(page.url().match(/runs\/(\d+)/)?.[1]),
    ) ?? approvalItems[0];
  expect(approval).toBeTruthy();
  const adminAuth = (await (
    await page.request.get(`${process.env.E2E_API_URL}/api/auth/me`)
  ).json()) as { csrf_token: string };
  const deniedDecision = await page.request.post(
    `${process.env.E2E_API_URL}/api/approvals/${approval.id}/decision`,
    {
      data: { decision: "approved" },
      headers: {
        Origin: process.env.E2E_WEB_URL!,
        "X-CSRF-Token": adminAuth.csrf_token,
      },
    },
  );
  expect(deniedDecision.status()).toBe(403);
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6approver", "User1!pw");
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
  await expect(page.locator(".approval-card").first()).toContainText(
    "Append text",
  );
  await page
    .getByLabel("Decision reason optional")
    .fill("Reviewed the configured text input.");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator(".approval-card").first()).toContainText(
    "approved",
  );
  await expect(
    page
      .locator(".approval-card")
      .first()
      .getByRole("button", { name: "Approve" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6admin");
  await page.goto(`/runs/${approval.run_id}`);
  await expect(page.locator(".run-state").first()).toHaveText("succeeded");
  await expect(page.locator(".io-grid")).toContainText("request-approved");

  // Rejection path persists a failed terminal run and decision reason.
  await page.goto("/workflows");
  await page
    .getByRole("row")
    .filter({ hasText: "Approval acceptance" })
    .getByRole("button", { name: "Run" })
    .click();
  await expect(page.locator(".run-state").first()).toHaveText(
    "awaiting approval",
  );
  const secondRunId = Number(page.url().match(/runs\/(\d+)/)?.[1]);
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6approver", "User1!pw");
  await page.goto("/approvals");
  await page
    .getByLabel("Decision reason optional")
    .fill("Rejected for the negative test path.");
  await page.getByRole("button", { name: "Reject" }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "phase6admin");
  await page.goto(`/runs/${secondRunId}`);
  await expect(page.locator(".run-state").first()).toHaveText("failed");
  await expect(page.locator(".run-detail")).toContainText("approval_rejected");

  // Layout checks at the requested desktop sizes and mobile width.
  await page.goto("/dashboard");
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(
    page.getByRole("heading", { name: "Welcome, phase6admin" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workflows/new");
  for (const width of [390]) {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflow, `viewport ${width} should not scroll horizontally`).toBe(
      false,
    );
  }
  await page.screenshot({
    path: "/tmp/agentflow-ux-390x844.png",
    fullPage: false,
  });
  const dashboardLink = page.getByRole("link", { name: "Dashboard" });
  await dashboardLink.focus();
  await expect(dashboardLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(
    page.getByRole("heading", { name: "Welcome, phase6admin" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
