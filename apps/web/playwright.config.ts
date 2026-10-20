import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: "line",
  outputDir: process.env.E2E_OUTPUT_DIR ?? "/tmp/agentflow-playwright-results",
  use: {
    baseURL: process.env.E2E_WEB_URL ?? "http://127.0.0.1:13000",
    browserName: "chromium",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: {
      ...(process.env.PLAYWRIGHT_CHROME_PATH
        ? { executablePath: process.env.PLAYWRIGHT_CHROME_PATH }
        : {}),
      args: ["--no-sandbox"],
    },
  },
});
