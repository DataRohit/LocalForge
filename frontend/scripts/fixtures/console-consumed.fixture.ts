/**
 * Fixture: a test that logs and then consumes the calls.
 *
 * Run only by the console policy test, which requires this one to pass: a test that expects a log
 * says so by taking the calls, and the shared setup then has nothing left to fail on.
 *
 * @packageDocumentation
 */

import { expect, it } from "vitest";

import { takeConsoleCalls } from "../../test/console";

it("logs both levels and consumes them", () => {
  console.error("expected failure");
  console.warn("expected warning");

  expect(takeConsoleCalls()).toEqual([
    "console.error: expected failure",
    "console.warn: expected warning",
  ]);
});
