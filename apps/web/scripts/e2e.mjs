import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(webRoot, "../..");
const scratch = mkdtempSync(resolve(tmpdir(), "agentflow-phase6-e2e-"));
const isolatedWebRoot = resolve(scratch, "web");
const databasePath = resolve(scratch, "agentflow.db");
const apiPort = "18001";
const webPort = "13000";
const apiUrl = `http://localhost:${apiPort}`;
const webUrl = `http://localhost:${webPort}`;
const password = "E2E-only.AgentFlow!2026";
const python = resolve(root, ".venv/bin/python");
const runtimeEnv = {
  ...process.env,
  DATABASE_URL: `sqlite:///${databasePath}`,
  API_HOST: "127.0.0.1",
  API_PORT: apiPort,
  CORS_ORIGINS: JSON.stringify([webUrl]),
  SESSION_COOKIE_SECURE: "false",
  APPROVAL_REQUIRED_ACTIONS: JSON.stringify(["append"]),
  NEXT_PUBLIC_API_URL: apiUrl,
  E2E_WEB_URL: webUrl,
  E2E_API_URL: apiUrl,
  E2E_PYTHON: python,
  E2E_ROOT: root,
  E2E_OUTPUT_DIR: resolve(scratch, "test-results"),
  E2E_PASSWORD: password,
  PLAYWRIGHT_CHROME_PATH:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/usr/bin/google-chrome",
};
const processes = [];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? root,
    env: runtimeEnv,
    encoding: "utf8",
    stdio: options.stdio ?? "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with status ${result.status}`);
  }
}

function prepareIsolatedWeb() {
  mkdirSync(isolatedWebRoot, { recursive: true });
  for (const file of [
    "package.json",
    "package-lock.json",
    "next.config.ts",
    "next-env.d.ts",
    "tsconfig.json",
    "playwright.config.ts",
  ]) {
    cpSync(resolve(webRoot, file), resolve(isolatedWebRoot, file));
  }
  for (const directory of ["src", "scripts", "tests"]) {
    cpSync(resolve(webRoot, directory), resolve(isolatedWebRoot, directory), {
      recursive: true,
    });
  }
  symlinkSync(
    resolve(webRoot, "node_modules"),
    resolve(isolatedWebRoot, "node_modules"),
    "dir",
  );
}

function start(command, args, cwd) {
  const child = spawn(command, args, {
    cwd,
    env: runtimeEnv,
    stdio: "inherit",
    detached: true,
  });
  processes.push(child);
  child.once("error", (error) => {
    console.error(`Could not start ${command}: ${error.message}`);
  });
  return child;
}

async function waitFor(url, child, label) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`${label} exited before becoming ready.`);
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch {
      // The service is expected to refuse connections until it finishes startup.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
  }
  throw new Error(`${label} did not become ready at ${url}.`);
}

async function stopAll() {
  for (const child of processes) {
    if (child.exitCode === null) {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {
        // The process may already have exited.
      }
    }
  }
  await Promise.all(
    processes.map(
      (child) =>
        new Promise((resolvePromise) => {
          if (child.exitCode !== null) return resolvePromise();
          const timer = setTimeout(() => {
            try {
              process.kill(-child.pid, "SIGKILL");
            } catch {
              // The process may already have exited.
            }
            resolvePromise();
          }, 5000);
          child.once("exit", () => {
            clearTimeout(timer);
            resolvePromise();
          });
        }),
    ),
  );
}

async function assertPortAvailable(port) {
  await new Promise((resolvePromise, reject) => {
    const server = createServer();
    server.once("error", (error) => reject(error));
    server.listen(port, "127.0.0.1", () => {
      server.close((error) => (error ? reject(error) : resolvePromise()));
    });
  });
}

let interrupted = false;
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    interrupted = true;
    void stopAll().finally(() => {
      rmSync(scratch, { recursive: true, force: true });
      process.exit(signal === "SIGINT" ? 130 : 143);
    });
  });
}

let completed = false;
try {
  await assertPortAvailable(Number(apiPort));
  await assertPortAvailable(Number(webPort));
  run(python, ["-m", "alembic", "upgrade", "head"]);
  run(python, [
    "-c",
    [
      "import os",
      "from agentflow.auth import hash_password, normalize_login",
      "from agentflow.database import SessionLocal",
      "from agentflow.models import User, utcnow",
      "username = 'phase6admin'",
      "email = 'phase6admin@example.test'",
      "with SessionLocal() as session:",
      "    session.add(User(username=username, username_normalized=normalize_login(username), email=email, email_normalized=normalize_login(email), password_hash=hash_password(os.environ['E2E_PASSWORD']), role='admin', is_active=True, created_at=utcnow(), updated_at=utcnow()))",
      "    session.commit()",
    ].join("\n"),
  ]);

  prepareIsolatedWeb();
  const api = start(
    python,
    [
      "-m",
      "uvicorn",
      "agentflow.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      apiPort,
    ],
    root,
  );
  await waitFor(`${apiUrl}/health`, api, "API");
  const web = start(
    "npm",
    ["run", "dev", "--", "--webpack", "--port", webPort],
    isolatedWebRoot,
  );
  await waitFor(webUrl, web, "dashboard");

  run(
    resolve(webRoot, "node_modules/.bin/playwright"),
    ["test", "--config=playwright.config.ts"],
    {
      cwd: isolatedWebRoot,
    },
  );
  completed = true;
} finally {
  if (!interrupted) await stopAll();
  if (completed || interrupted) {
    rmSync(scratch, { recursive: true, force: true });
  } else {
    console.error(`E2E artifacts retained at ${scratch}`);
  }
}
