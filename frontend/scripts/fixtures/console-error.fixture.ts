/**
 * Fixture: a test that calls console.error.
 *
 * Run only by the console policy test, which requires the shared setup to fail it.
 * It is not named as a test file, so the normal suite never runs it.
 *
 * @packageDocumentation
 */

import { it } from "vitest";

it("logs an error and does not consume it", () => {
  console.error("unexpected failure");
});
