import { defineConfig } from "@playwright/test";
import { version } from "./package.json";

const results =
  process.env.RACP_TEST_RESULTS ??
  `../../dist/verification/console/${version}/e2e-${Date.now()}`;

export default defineConfig({
  testDir: "./tests",
  outputDir: `${results}/test-results`,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 10000 },
  reporter: [
    ["list"],
    ["junit", { outputFile: `${results}/test-results.xml` }],
  ],
  use: {
    baseURL: process.env.RACP_BASE_URL,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
