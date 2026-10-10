/**
 * Tests for the interface hooks.
 *
 * Covers the document title lifecycle and the online status subscription, including both
 * browser events and the cleanup of their listeners.
 *
 * @packageDocumentation
 */

import { act, render, renderHook, screen } from "@testing-library/react";
import type { JSX } from "react";
import { describe, expect, it, vi } from "vitest";

import { usePageTitle } from "./usePageTitle";
import { useOnlineStatus } from "./useOnlineStatus";

function Title({ title }: { title: string }): null {
  usePageTitle(title);
  return null;
}

function Status(): JSX.Element {
  return <p>{useOnlineStatus() ? "online" : "offline"}</p>;
}

describe("usePageTitle", () => {
  it("sets the title, follows changes, and restores the previous title", () => {
    document.title = "Before";
    const { rerender, unmount } = render(<Title title="One" />);
    expect(document.title).toBe("One · LocalForge");

    rerender(<Title title="Two" />);
    expect(document.title).toBe("Two · LocalForge");

    unmount();
    expect(document.title).toBe("Before");
  });
});

describe("useOnlineStatus", () => {
  it("follows the browser online and offline events", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    render(<Status />);
    expect(screen.getByText("online")).toBeInTheDocument();

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByText("offline")).toBeInTheDocument();

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.getByText("online")).toBeInTheDocument();
  });

  it("removes its listeners on unmount", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(() => useOnlineStatus());

    unmount();

    expect(remove).toHaveBeenCalledWith("online", expect.any(Function));
    expect(remove).toHaveBeenCalledWith("offline", expect.any(Function));
  });
});
