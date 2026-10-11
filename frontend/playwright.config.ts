/**
 * Playwright configuration.
 *
 * Always defines the audit project, which serves the production build over HTTP and mocks the API
 * at the network layer. When the end-to-end runner sets LOCALFORGE_E2E_JOURNEYS it also starts the
 * host-mode testing backend and an HTTPS origin and adds the journey projects for three browsers.
 *
 * @packageDocumentation
 */

import { defineConfig, devices } from "@playwright/test";
import type { PlaywrightTestConfig } from "@playwright/test";

const HTTP_ORIGIN = "http://127.0.0.1:4173";
const HTTPS_ORIGIN = "https://127.0.0.1:4443";
const BACKEND_READY = "http://127.0.0.1:28000/api/v1/browser/csrf/";
const journeys = process.env.LOCALFORGE_E2E_JOURNEYS === "1";

const PREVIEW = "pnpm exec vite preview --host 127.0.0.1 --strictPort --port";

const servers: NonNullable<PlaywrightTestConfig["webServer"]> = [
  { command: `${PREVIEW} 4173`, url: HTTP_ORIGIN, reuseExistingServer: false, timeout: 60_000 },
];
if (journeys) {
  servers.push(
    {
      command: "node e2e/support/server.ts",
      url: BACKEND_READY,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `${PREVIEW} 4443`,
      url: HTTPS_ORIGIN,
      ignoreHTTPSErrors: true,
      env: { LOCALFORGE_E2E_TLS_DIRECTORY: process.env.E2E_TLS_DIRECTORY ?? "" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  );
}

/**
 * Playwright configuration object.
 *
 * Runs files in parallel with per-test timeouts, blocks service workers, and starts every server
 * fresh so a test never talks to a stale build or backend.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: true,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  ...(journeys ? { workers: 4 } : {}),
  reporter: [["list"], ["json", { outputFile: ".e2e-run/results.json" }]],
  use: { baseURL: HTTP_ORIGIN, serviceWorkers: "block", trace: "off" },
  webServer: servers,
  projects: [
    {
      name: "audit-chromium",
      testMatch: /audit\/.*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    ...(journeys
      ? (["chromium", "firefox", "webkit"] as const).map((browser) => ({
          name: `journeys-${browser}`,
          testMatch: /journeys\/.*\.spec\.ts/,
          use: {
            ...devices[
              { chromium: "Desktop Chrome", firefox: "Desktop Firefox", webkit: "Desktop Safari" }[
                browser
              ]
            ],
            baseURL: HTTPS_ORIGIN,
            ignoreHTTPSErrors: true,
          },
        }))
      : []),
  ],
});
