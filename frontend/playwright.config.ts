/**
 * Playwright configuration.
 *
 * Serves the production build with Vite preview on loopback and defines the browser projects. The
 * audit project mocks the API at the network layer; journey projects are added with Ticket 96.
 *
 * @packageDocumentation
 */

import { defineConfig, devices } from "@playwright/test";

const ORIGIN = "http://127.0.0.1:4173";

/**
 * Playwright configuration object.
 *
 * Runs files in parallel with a per-test timeout, blocks service workers, and starts the preview
 * server fresh so a test never talks to a stale build.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: true,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: ORIGIN,
    serviceWorkers: "block",
    trace: "off",
  },
  webServer: {
    command: "pnpm exec vite preview --host 127.0.0.1 --port 4173 --strictPort",
    url: ORIGIN,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    {
      name: "audit-chromium",
      testMatch: /audit\/.*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
