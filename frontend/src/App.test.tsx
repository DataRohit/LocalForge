/**
 * Tests for the application root component.
 *
 * Confirms the landing heading renders inside the main landmark.
 * It guards the accessible structure before the router and session arrive.
 *
 * @packageDocumentation
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the product heading in the main landmark", () => {
    render(<App />);

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "LocalForge" })).toBeInTheDocument();
  });
});
