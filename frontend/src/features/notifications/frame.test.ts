/**
 * Tests for notification frames.
 *
 * Covers the well-formed frame and every kind of malformed message, which must be ignored, and the
 * wording for known and unknown events.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { notificationFrame } from "../../../test/socket";
import { describeEvent, parseNotification } from "./frame";

describe("parseNotification", () => {
  it("returns the event name of a notification frame", () => {
    expect(parseNotification(notificationFrame("account.updated", { revision: 2 }))).toBe(
      "account.updated",
    );
    expect(parseNotification(notificationFrame("account.username_changed"))).toBe(
      "account.username_changed",
    );
  });

  it.each([
    ["binary data", new ArrayBuffer(4)],
    ["a number", 5],
    ["invalid JSON", "{not json"],
    ["a JSON string", '"hello"'],
    ["a JSON array", "[]"],
    ["a JSON null", "null"],
    [
      "an error frame",
      JSON.stringify({ type: "error", payload: { code: "unknown_message_type" } }),
    ],
    ["an unknown type", JSON.stringify({ type: "other", payload: { event: "x", data: {} } })],
    ["a missing payload", JSON.stringify({ type: "notification" })],
    ["a payload that is not an object", JSON.stringify({ type: "notification", payload: 5 })],
    ["an empty event", JSON.stringify({ type: "notification", payload: { event: "", data: {} } })],
    ["a numeric event", JSON.stringify({ type: "notification", payload: { event: 5, data: {} } })],
    [
      "data that is not an object",
      JSON.stringify({ type: "notification", payload: { event: "x", data: [] } }),
    ],
    ["missing data", JSON.stringify({ type: "notification", payload: { event: "x" } })],
  ])("ignores %s", (_name, data) => {
    expect(parseNotification(data)).toBeNull();
  });
});

describe("describeEvent", () => {
  it("uses a sentence for the documented events", () => {
    expect(describeEvent("account.updated")).toBe("Your account was updated.");
    expect(describeEvent("account.username_changed")).toBe("Your username was changed.");
  });

  it("shows an unknown event name as plain text", () => {
    expect(describeEvent("report.ready")).toBe("Update received: report.ready");
    expect(describeEvent("<b>x</b>")).toBe("Update received: <b>x</b>");
  });
});
