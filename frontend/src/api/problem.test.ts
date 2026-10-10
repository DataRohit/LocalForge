/**
 * Tests for the error presentation model.
 *
 * Maps every documented status of every exposed operation to a kind, and covers the non-API
 * error classes and unknown thrown values.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { envelope } from "../../test/backend";
import { exposedOperations } from "./endpoints";
import { ApiError, NetworkError, UnexpectedResponseError } from "./errors";
import { operations } from "./generated/operations";
import { toProblem, type ProblemKind } from "./problem";

const EXPECTED: Record<number, ProblemKind> = {
  400: "validation",
  401: "unauthorized",
  403: "forbidden",
  404: "notFound",
  405: "other",
  406: "other",
  413: "other",
  415: "other",
  429: "throttled",
  500: "server",
  503: "server",
};

describe("toProblem", () => {
  it("maps every documented error status of every exposed operation to a kind", () => {
    const statuses = new Set(
      exposedOperations.flatMap((id) => operations[id].statuses as readonly number[]),
    );

    for (const status of [...statuses].filter((value) => value >= 400)) {
      const kind = EXPECTED[status];
      expect(kind, `status ${String(status)} has no expected kind`).toBeDefined();
      expect(toProblem(new ApiError(status, envelope("c", "Message."), null)).kind).toBe(kind);
    }
  });

  it("classifies a 404 as not found even though no exposed operation documents one", () => {
    expect(toProblem(new ApiError(404, envelope("not_found", "Missing."), null)).kind).toBe(
      "notFound",
    );
  });

  it("keeps the backend message, fields, code, request id, and wait for client errors", () => {
    const sent = envelope("validation_error", "Fix the fields.", { email: ["Invalid."] });

    expect(toProblem(new ApiError(400, sent, null))).toEqual({
      kind: "validation",
      message: "Fix the fields.",
      fields: { email: ["Invalid."] },
      code: "validation_error",
      requestId: sent.request_id,
      retryAfterSeconds: null,
    });
    expect(toProblem(new ApiError(429, envelope("throttled", "Slow."), 12))).toMatchObject({
      kind: "throttled",
      retryAfterSeconds: 12,
    });
  });

  it("replaces server failure text with fixed wording but keeps the identifiers", () => {
    const problem = toProblem(
      new ApiError(503, envelope("unavailable", "db down at 10.0.0.5"), null),
    );

    expect(problem.kind).toBe("server");
    expect(problem.message).toBe("The service is unavailable right now. Try again in a moment.");
    expect(problem.code).toBe("unavailable");
  });

  it("maps a network failure to offline", () => {
    expect(toProblem(new NetworkError(new TypeError("x")))).toMatchObject({
      kind: "offline",
      message: "You appear to be offline. Check your connection and try again.",
      fields: {},
      code: null,
    });
  });

  it.each([
    ["an unexpected response", new UnexpectedResponseError("odd", 418)],
    ["a plain error", new Error("boom")],
    ["a thrown string", "boom"],
    ["undefined", undefined],
  ])("maps %s to unexpected without leaking its text", (_name, value) => {
    const problem = toProblem(value);

    expect(problem.kind).toBe("unexpected");
    expect(problem.message).toBe("Something unexpected happened. Try again in a moment.");
  });
});
