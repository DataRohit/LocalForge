/**
 * Shared test setup.
 *
 * Registers the DOM matchers, runs the mock backend, unmounts rendered trees, and fails any test
 * that leaves a console.error or console.warn call unconsumed.
 *
 * @packageDocumentation
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect, vi } from "vitest";

import { server } from "./backend";
import { recordConsoleCall, takeConsoleCalls } from "./console";

const ASYNC_TIMEOUT_MS = 5000;

configure({ asyncUtilTimeout: ASYNC_TIMEOUT_MS });

beforeAll(() => {
  server.listen({ onUnhandledFrame: "error" });
});

afterAll(() => {
  server.close();
});

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
  server.resetHandlers();
  const calls = takeConsoleCalls();
  vi.restoreAllMocks();
  expect(calls).toEqual([]);
});
