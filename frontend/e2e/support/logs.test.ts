/**
 * Tests for log classification.
 *
 * Covers expected, unexplained, and empty windows.
 * An unexplained line must always reach the failure list.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { classify, EXPECTED_WARNINGS, serverWarnings } from "./logs.ts";

const RULES = [{ pattern: /Forbidden \(CSRF/, reason: "the wrong-token journey" }];

describe("classify", () => {
  it("separates provoked lines from unexplained ones", () => {
    const result = classify(
      ["server: Forbidden (CSRF token incorrect)", "postgres: ERROR boom"],
      RULES,
    );

    expect(result.expected).toEqual([
      "server: Forbidden (CSRF token incorrect) (the wrong-token journey)",
    ]);
    expect(result.unexplained).toEqual(["postgres: ERROR boom"]);
  });

  it("reports nothing for an empty window", () => {
    expect(classify([], RULES)).toEqual({ expected: [], unexplained: [] });
  });

  it("uses the recorded expectations by default", () => {
    expect(
      classify(["x: WARNING y"]).unexplained.length + classify(["x: WARNING y"]).expected.length,
    ).toBe(1);
    expect(EXPECTED_WARNINGS.every((rule) => rule.reason.length > 0)).toBe(true);
  });
});

describe("serverWarnings", () => {
  it("describes warning-level records by level, logger, message, method, path, and status", () => {
    const text = [
      '{"level": "WARNING", "logger": "django.request", "message": "Unauthorized: ****", "request": {"method": "GET", "path": "/api/v1/users/me/"}, "status_code": 401}',
      '{"level": "ERROR", "logger": "x", "message": "bad", "http_method": "POST", "http_path": "/p/", "http_status": 500}',
      '{"level": "CRITICAL"}',
      '{"level": "INFO", "message": "fine"}',
      "",
    ].join("\n");

    expect(serverWarnings(text)).toEqual([
      'host-mode server: WARNING django.request "Unauthorized: ****" GET /api/v1/users/me/ 401',
      'host-mode server: ERROR x "bad" POST /p/ 500',
      'host-mode server: CRITICAL - "-" - - -',
    ]);
  });

  it("keeps a plain-text warning line and ignores plain-text information", () => {
    expect(serverWarnings("Traceback WARNING stray\nINFO ok\n123\nnull")).toEqual([
      "host-mode server: Traceback WARNING stray",
    ]);
  });
});

describe("recorded expectations", () => {
  const described = (method: string, path: string, status: number): string =>
    `host-mode server: WARNING django.request "Forbidden: x" ${method} ${path} ${status}`;

  it.each([
    ["GET", "/api/v1/users/me/", 401],
    ["POST", "/api/v1/browser/refresh/", 401],
    ["POST", "/api/v1/browser/login/", 401],
    ["PATCH", "/api/v1/users/me/", 403],
    ["POST", "/api/v1/users/resend_activation/", 429],
  ])("accepts %s %s %i", (method, path, status) => {
    const result = classify([described(method, path, status)]);

    expect(result.unexplained).toEqual([]);
    expect(result.expected).toHaveLength(1);
  });

  it.each([
    ["GET", "/api/v1/users/me/", 500],
    ["POST", "/api/v1/browser/refresh/", 403],
    ["DELETE", "/api/v1/users/me/", 403],
    ["POST", "/api/v1/users/set_password/", 429],
  ])("does not accept %s %s %i", (method, path, status) => {
    expect(classify([described(method, path, status)]).unexplained).toHaveLength(1);
  });

  it("never accepts a stream failure or an error-level record", () => {
    const lines = [
      'host-mode server: WARNING localforge.request "request stream failed" POST /api/v1/browser/refresh/ 500',
      'host-mode server: ERROR django.request "Internal Server Error" GET /api/v1/users/me/ 401',
    ];

    expect(classify(lines).unexplained).toEqual(lines);
  });
});
