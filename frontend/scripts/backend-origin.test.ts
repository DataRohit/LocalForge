/**
 * Tests for the development proxy origin guard.
 *
 * Proves the default targets the testing band and every development port is refused.
 * Malformed values fail instead of falling back.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { DEFAULT_BACKEND_ORIGIN, resolveBackendOrigin } from "./backend-origin.ts";

describe("resolveBackendOrigin", () => {
  it("uses the testing default when no value is supplied", () => {
    expect(resolveBackendOrigin(undefined)).toBe(DEFAULT_BACKEND_ORIGIN);
  });

  it("normalizes a supplied testing origin", () => {
    expect(resolveBackendOrigin("http://localhost:28010/ignored/path")).toBe(
      "http://localhost:28010",
    );
  });

  it.each(["8000", "8080", "8081"])("refuses development port %s", (port) => {
    expect(() => resolveBackendOrigin(`http://127.0.0.1:${port}`)).toThrow(/development/);
  });

  it("rejects a value that is not a URL", () => {
    expect(() => resolveBackendOrigin("not a url")).toThrow(TypeError);
  });
});
