/**
 * Shared test setup.
 *
 * Registers the DOM matchers, runs the mock backend, unmounts rendered trees, restores the shared
 * online state, and fails any test that leaves a console.error or console.warn call unconsumed.
 *
 * @packageDocumentation
 */

import "@testing-library/jest-dom/vitest";
import { onlineManager } from "@tanstack/react-query";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect, vi } from "vitest";

import { server } from "./backend";
import "./dialog";
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
  onlineManager.setOnline(true);
  const calls = takeConsoleCalls();
  vi.restoreAllMocks();
  expect(calls).toEqual([]);
});
