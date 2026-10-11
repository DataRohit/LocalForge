/**
 * Tests for the dialog primitive.
 *
 * Covers opening, closing, the accessible name, rendering content only while open, Escape, and
 * returning focus to the opener, including when no element held focus.
 *
 * @packageDocumentation
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { JSX } from "react";
import { describe, expect, it, vi } from "vitest";

import { Dialog } from "./Dialog";

function Harness(): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        onClick={() => {
          setOpen(true);
        }}
      >
        open it
      </button>
      <Dialog
        title="Are you sure?"
        open={open}
        onClose={() => {
          setOpen(false);
        }}
      >
        <input aria-label="inside" />
        <button
          onClick={() => {
            setOpen(false);
          }}
        >
          dismiss
        </button>
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("is hidden and renders no content until opened", () => {
    render(<Harness />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("inside")).not.toBeInTheDocument();
  });

  it("opens as a named modal and moves focus inside", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "open it" }));

    const dialog = screen.getByRole("dialog", { name: "Are you sure?" });
    expect(dialog).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "Are you sure?" })).toBeInTheDocument();
    expect(screen.getByLabelText("inside")).toHaveFocus();
  });

  it("closes and returns focus to the control that opened it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "open it" });
    await user.click(opener);

    await user.click(screen.getByRole("button", { name: "dismiss" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("asks its owner to close on Escape instead of closing itself", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "open it" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog");
    const event = new Event("cancel", { cancelable: true });

    fireEvent(dialog, event);

    expect(event.defaultPrevented).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("resets its content each time it opens", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "open it" }));
    await user.type(screen.getByLabelText("inside"), "typed");
    await user.click(screen.getByRole("button", { name: "dismiss" }));

    await user.click(screen.getByRole("button", { name: "open it" }));

    expect(screen.getByLabelText("inside")).toHaveValue("");
  });

  it("closes cleanly when nothing held focus before it opened", () => {
    render(<Harness />);
    vi.spyOn(document, "activeElement", "get").mockReturnValue(null);

    fireEvent.click(screen.getByRole("button", { name: "open it" }));
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
