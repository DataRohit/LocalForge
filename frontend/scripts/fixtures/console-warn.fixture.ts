/**
 * Fixture: a test that calls console.warn.
 *
 * Run only by the console policy test, which requires the shared setup to fail it.
 * It is not named as a test file, so the normal suite never runs it.
 *
 * @packageDocumentation
 */

import { it } from "vitest";

it("logs a warning and does not consume it", () => {
  console.warn("suspicious state");
});
