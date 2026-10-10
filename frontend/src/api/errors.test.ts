/**
 * Tests for the API error types.
 *
 * Covers the envelope parser against valid and malformed bodies and the three error classes.
 * A proxy page or truncated body must never be accepted as a backend error.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { ApiError, NetworkError, UnexpectedResponseError, parseEnvelope } from "./errors";

const VALID = {
  code: "validation_error",
  message: "Invalid.",
  details: { email: ["Enter a valid address."] },
  request_id: "00000000-0000-4000-8000-000000000000",
};

describe("parseEnvelope", () => {
  it("accepts a complete envelope and drops unknown keys", () => {
    expect(parseEnvelope({ ...VALID, extra: 1 })).toEqual(VALID);
  });

  it("accepts an empty details object", () => {
    expect(parseEnvelope({ ...VALID, details: {} })?.details).toEqual({});
  });

  it.each([
    ["null", null],
    ["a string", "<html>Bad gateway</html>"],
    ["an array", []],
    ["a missing code", { ...VALID, code: undefined }],
    ["a numeric message", { ...VALID, message: 5 }],
    ["details that are not arrays of strings", { ...VALID, details: { email: "bad" } }],
    ["a missing request id", { ...VALID, request_id: undefined }],
  ])("rejects %s", (_name, value) => {
    expect(parseEnvelope(value)).toBeNull();
  });
});

describe("ApiError", () => {
  it("copies the envelope onto the error", () => {
    const error = new ApiError(429, VALID, 17);

    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("Invalid.");
    expect(error.status).toBe(429);
    expect(error.code).toBe("validation_error");
    expect(error.details).toEqual(VALID.details);
    expect(error.requestId).toBe(VALID.request_id);
    expect(error.retryAfterSeconds).toBe(17);
  });
});

describe("NetworkError", () => {
  it("keeps the cause", () => {
    const cause = new TypeError("fetch failed");
    const error = new NetworkError(cause);

    expect(error.name).toBe("NetworkError");
    expect(error.cause).toBe(cause);
  });
});

describe("UnexpectedResponseError", () => {
  it("carries the reason and status", () => {
    const error = new UnexpectedResponseError("odd", 418);

    expect(error.name).toBe("UnexpectedResponseError");
    expect(error.message).toBe("odd");
    expect(error.status).toBe(418);
    expect(new UnexpectedResponseError("none", null).status).toBeNull();
  });
});
