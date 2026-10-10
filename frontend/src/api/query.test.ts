/**
 * Tests for the query client setup.
 *
 * Covers the retry policy and the defaults the factory applies.
 * Only network failures may be retried; an HTTP error is an answer.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { envelope } from "../../test/backend";
import { ApiError, NetworkError } from "./errors";
import { createQueryClient, queryKeys, shouldRetry } from "./query";

describe("shouldRetry", () => {
  it("retries network failures a bounded number of times", () => {
    const error = new NetworkError(new TypeError("x"));

    expect(shouldRetry(0, error)).toBe(true);
    expect(shouldRetry(1, error)).toBe(true);
    expect(shouldRetry(2, error)).toBe(false);
  });

  it("never retries a documented HTTP error or an unknown error", () => {
    expect(shouldRetry(0, new ApiError(500, envelope("c", "m"), null))).toBe(false);
    expect(shouldRetry(0, new Error("x"))).toBe(false);
  });
});

describe("createQueryClient", () => {
  it("applies the retry policy to queries and disables mutation retries", () => {
    const defaults = createQueryClient().getDefaultOptions();

    expect(defaults.queries?.retry).toBe(shouldRetry);
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false);
    expect(defaults.queries?.staleTime).toBe(30_000);
    expect(defaults.mutations?.retry).toBe(false);
  });

  it("returns a distinct client each time", () => {
    expect(createQueryClient()).not.toBe(createQueryClient());
  });
});

describe("queryKeys", () => {
  it("names the profile resource", () => {
    expect(queryKeys.me).toEqual(["me"]);
  });
});
