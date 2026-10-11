/**
 * Tests for the problem alert.
 *
 * Covers the title for each failure kind, the hint after a refused request, the wait time, the
 * quotable reference, and extra detail lines.
 *
 * @packageDocumentation
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProblemAlert, type ProblemView } from "./ProblemAlert";

function problem(overrides: Partial<ProblemView>): ProblemView {
  return {
    kind: "other",
    message: "It went wrong.",
    retryAfterSeconds: null,
    requestId: null,
    ...overrides,
  };
}

describe("ProblemAlert", () => {
  it.each([
    ["offline", "You are offline"],
    ["throttled", "Too many attempts"],
    ["validation", "Please correct the form"],
    ["forbidden", "Request refused"],
    ["server", "The request failed"],
    ["anything-else", "The request failed"],
  ])("titles a %s problem %s", (kind, title) => {
    render(<ProblemAlert problem={problem({ kind })} />);

    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByText("It went wrong.")).toBeInTheDocument();
  });

  it("tells the person to reload after a refused request, and only then", () => {
    const { rerender } = render(<ProblemAlert problem={problem({ kind: "forbidden" })} />);

    expect(screen.getByText("Reload the page, then try again.")).toBeInTheDocument();

    rerender(<ProblemAlert problem={problem({ kind: "server" })} />);

    expect(screen.queryByText("Reload the page, then try again.")).not.toBeInTheDocument();
  });

  it("shows the wait time and the reference, but no reference for validation", () => {
    const { rerender } = render(
      <ProblemAlert
        problem={problem({ kind: "throttled", retryAfterSeconds: 30, requestId: "req-1" })}
      />,
    );

    expect(screen.getByText("Try again in 30 seconds.")).toBeInTheDocument();
    expect(screen.getByText("Reference: req-1")).toBeInTheDocument();

    rerender(<ProblemAlert problem={problem({ kind: "validation", requestId: "req-2" })} />);

    expect(screen.queryByText(/Reference/)).not.toBeInTheDocument();
  });

  it("lists detail lines for fields the form does not have", () => {
    render(<ProblemAlert problem={problem({ kind: "validation" })} details={["Try another."]} />);

    expect(screen.getByText("Try another.")).toBeInTheDocument();
  });
});
