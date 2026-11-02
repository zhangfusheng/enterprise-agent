import { defineConfig } from "@playwright/test";
import path from "node:path";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  use: {
    baseURL: "http://127.0.0.1:5175",
    headless: true,
    viewport: { width: 1500, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "uv run --no-sync python -m uvicorn tests.support.trace_preview:create_app --factory --host 127.0.0.1 --port 8765",
      cwd: path.resolve(import.meta.dirname, "../.."),
      url: "http://127.0.0.1:8765/api/v1/traces",
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: "npm run dev -- --port 5175",
      env: { TRACE_API_TARGET: "http://127.0.0.1:8765" },
      url: "http://127.0.0.1:5175",
      reuseExistingServer: false,
      timeout: 60000,
    },
  ],
});
