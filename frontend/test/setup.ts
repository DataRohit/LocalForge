/**
 * Shared test setup.
 *
 * Registers the DOM matchers, unmounts rendered trees, and fails any test that leaves a
 * console.error or console.warn call unconsumed.
 *
 * @packageDocumentation
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import { recordConsoleCall, takeConsoleCalls } from "./console";

beforeEach(() => {
  takeConsoleCalls();
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    recordConsoleCall("error", args);
  });
  vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    recordConsoleCall("warn", args);
  });
});

afterEach(() => {
  cleanup();
  const calls = takeConsoleCalls();
  vi.restoreAllMocks();
  expect(calls).toEqual([]);
});
