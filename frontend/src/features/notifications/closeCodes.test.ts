/**
 * Tests for the close code policy.
 *
 * Walks every close code the notification contract documents and the transport codes the server can
 * emit, and asserts the one action each maps to.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { classifyClose, THROTTLE_WAIT_MS } from "./closeCodes";

describe("classifyClose", () => {
  it.each([4401, 4402, 4403])("renews the session for %i", (code) => {
    expect(classifyClose(code)).toEqual({ kind: "renew" });
  });

  it("waits out the throttle window for 4408", () => {
    expect(classifyClose(4408)).toEqual({ kind: "wait", delayMs: THROTTLE_WAIT_MS });
    expect(THROTTLE_WAIT_MS).toBe(60_000);
  });

  it.each([4400, 4404, 4405, 4406, 4407, 1002, 1007, 1009])(
    "stops for %i with a message",
    (code) => {
      const action = classifyClose(code);

      expect(action.kind).toBe("stop");
      expect(action).toHaveProperty("message");
      expect(action.kind === "stop" ? action.message.length : 0).toBeGreaterThan(10);
    },
  );

  it("gives the account codes their own wording", () => {
    const message = (code: number): string => {
      const action = classifyClose(code);
      return action.kind === "stop" ? action.message : "";
    };

    expect(message(4404)).toContain("not active");
    expect(message(4405)).toContain("no longer exists");
    expect(message(4406)).toContain("not allowed");
    expect(message(4400)).toContain("protocol error");
  });

  it.each([4500, 1011, 1012, 1006, 1000, 1001, 3000, 4999])(
    "retries with backoff for %i",
    (code) => {
      expect(classifyClose(code)).toEqual({ kind: "retry" });
    },
  );
});
