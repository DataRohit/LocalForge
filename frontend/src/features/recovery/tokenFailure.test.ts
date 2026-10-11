/**
 * Tests for recovery token failure messages.
 *
 * Covers the four documented codes of each family and every non-matching input.
 * A validation failure must never be mistaken for a dead link.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { tokenFailureMessage } from "./tokenFailure";

describe("tokenFailureMessage", () => {
  it.each([
    ["expired", "This link has expired. Request a new one."],
    ["used", "This link was already used. Request a new one if you still need it."],
    ["foreign", "This link is not valid. Request a new one."],
    ["malformed", "This link is not valid. Request a new one."],
  ])("explains %s for both families", (suffix, message) => {
    expect(tokenFailureMessage("password", `password_reset_token_${suffix}`)).toBe(message);
    expect(tokenFailureMessage("username", `username_reset_token_${suffix}`)).toBe(message);
  });

  it("does not treat the other family's code as a token failure", () => {
    expect(tokenFailureMessage("password", "username_reset_token_expired")).toBeNull();
    expect(tokenFailureMessage("username", "password_reset_token_expired")).toBeNull();
  });

  it.each([null, "validation_error", "password_reset_token_unknown", "password_reset_token_"])(
    "returns null for %s",
    (code) => {
      expect(tokenFailureMessage("password", code)).toBeNull();
    },
  );
});
