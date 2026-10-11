/**
 * Vitest configuration for the console policy fixtures.
 *
 * Runs only the fixture files with the project's shared test setup, so the policy that fails a test
 * on a console call can be observed in a real run rather than assumed.
 *
 * @packageDocumentation
 */

import path from "node:path";

import { defineConfig } from "vitest/config";

/**
 * Fixture configuration object.
 *
 * Roots the run at the project, selects the fixture files only, and loads the shared setup.
 */
export default defineConfig({
  root: path.resolve(import.meta.dirname, "..", ".."),
  test: {
    environment: "jsdom",
    include: ["scripts/fixtures/*.fixture.ts"],
    setupFiles: ["test/setup.ts"],
    testTimeout: 10_000,
  },
});
