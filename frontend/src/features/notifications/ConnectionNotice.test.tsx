/**
 * Tests for the connection notice.
 *
 * Renders the notice for every feed status and checks its words, its role, and the retry button of
 * the stopped state. The idle status renders nothing.
 *
 * @packageDocumentation
 */

import { render, screen } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConnectionNotice } from "./ConnectionNotice";
import type { FeedStatus } from "./context";

function show(status: FeedStatus, reason: string | null = null, onRetry = vi.fn()): RenderResult {
  return render(<ConnectionNotice view={{ status, reason, items: [] }} onRetry={onRetry} />);
}

describe("ConnectionNotice", () => {
  it.each([
    ["connecting", "Connecting…"],
    ["open", "Live"],
    ["reconnecting", "Reconnecting…"],
    ["offline", "Notifications paused"],
  ] as const)("describes the %s status in words", (status, title) => {
    show(status);

    expect(screen.getByRole("status")).toHaveTextContent(title);
  });

  it("announces a stop as an alert with the reason and a retry button", async () => {
    const onRetry = vi.fn();
    show("stopped", "Your account is not active.", onRetry);

    expect(screen.getByRole("alert")).toHaveTextContent("Notifications stopped");
    expect(screen.getByRole("alert")).toHaveTextContent("Your account is not active.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when idle", () => {
    const { container } = show("idle");

    expect(container).toBeEmptyDOMElement();
  });
});
