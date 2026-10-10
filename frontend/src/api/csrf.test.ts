/**
 * Tests for CSRF cookie access.
 *
 * Covers exact-name matching, decoding, and the empty and absent cases.
 * A similarly named cookie must never be mistaken for the token.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { setCookies } from "../../test/cookies";
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME, readCookie, readCsrfToken } from "./csrf";

describe("readCookie", () => {
  it("finds a cookie by exact name", () => {
    expect(readCookie("a", "a=1; b=2")).toBe("1");
    expect(readCookie("b", "a=1; b=2")).toBe("2");
  });

  it("does not match a longer or shorter name", () => {
    expect(readCookie("a", "ab=1; xa=2")).toBeNull();
  });

  it("decodes percent-encoded values and keeps equals signs", () => {
    expect(readCookie("a", "a=x%20y%3Dz")).toBe("x y=z");
  });

  it("returns null for an empty value, a missing pair, or an empty string", () => {
    expect(readCookie("a", "a=")).toBeNull();
    expect(readCookie("a", "novalue; b=2")).toBeNull();
    expect(readCookie("a", "")).toBeNull();
  });

  it("reads the document cookies by default", () => {
    setCookies("session=abc");

    expect(readCookie("session")).toBe("abc");
  });
});

describe("readCsrfToken", () => {
  it("reads the frozen cookie name", () => {
    setCookies(`${CSRF_COOKIE_NAME}=token-1`);

    expect(readCsrfToken()).toBe("token-1");
    expect(CSRF_HEADER_NAME).toBe("X-CSRFToken");
  });

  it("returns null when the cookie is absent", () => {
    setCookies("other=1");

    expect(readCsrfToken()).toBeNull();
  });
});
