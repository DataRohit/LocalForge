/**
 * Tests for URL fragment credentials.
 *
 * Covers reading both credentials, removing the fragment from the history entry, partial and
 * oversized values, and fragments that carry no credentials at all.
 *
 * @packageDocumentation
 */

import { describe, expect, it, vi } from "vitest";

import { takeFragmentCredentials } from "./fragment";

function windowAt(
  hash: string,
  pathname = "/activate",
  search = "",
): {
  win: Parameters<typeof takeFragmentCredentials>[0];
  replaceState: ReturnType<typeof vi.fn>;
} {
  const replaceState = vi.fn();
  return {
    win: { location: { hash, pathname, search }, history: { replaceState, state: { key: 1 } } },
    replaceState,
  };
}

describe("takeFragmentCredentials", () => {
  it("returns both credentials and removes the fragment from the history entry", () => {
    const { win, replaceState } = windowAt("#account=abc-123&token=tok.en_1", "/activate", "?x=1");

    expect(takeFragmentCredentials(win)).toEqual({ account: "abc-123", token: "tok.en_1" });
    expect(replaceState).toHaveBeenCalledTimes(1);
    expect(replaceState).toHaveBeenCalledWith({ key: 1 }, "", "/activate?x=1");
  });

  it("decodes percent-encoded values", () => {
    const { win } = windowAt("#account=a%2Fb&token=t%20k");

    expect(takeFragmentCredentials(win)).toEqual({ account: "a/b", token: "t k" });
  });

  it.each([
    ["only the account", "#account=abc"],
    ["only the token", "#token=abc"],
    ["an empty account", "#account=&token=abc"],
    ["an empty token", "#account=abc&token="],
    ["an oversized token", `#account=abc&token=${"x".repeat(2049)}`],
    ["an oversized account", `#account=${"x".repeat(2049)}&token=abc`],
  ])("removes the fragment but returns nothing for %s", (_name, hash) => {
    const { win, replaceState } = windowAt(hash);

    expect(takeFragmentCredentials(win)).toBeNull();
    expect(replaceState).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["an empty fragment", ""],
    ["a bare hash", "#"],
    ["an unrelated fragment", "#section-2"],
  ])("leaves %s untouched", (_name, hash) => {
    const { win, replaceState } = windowAt(hash);

    expect(takeFragmentCredentials(win)).toBeNull();
    expect(replaceState).not.toHaveBeenCalled();
  });
});
