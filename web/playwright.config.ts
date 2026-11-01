import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:5173",
    channel: "chrome",
    trace: "retain-on-failure",
    video: process.env.E2E_M3 === "true" ? "retain-on-failure" : "off",
    screenshot: "only-on-failure",
  },
  reporter: "list",
  workers: process.env.E2E_M3 === "true" ? 1 : undefined,
});
