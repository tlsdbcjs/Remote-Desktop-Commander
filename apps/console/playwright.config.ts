import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 10000 },
  reporter: [
    ["list"],
    ["junit", { outputFile: "../../dist/console-test-results.xml" }],
  ],
  use: {
    baseURL: process.env.RACP_BASE_URL,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
