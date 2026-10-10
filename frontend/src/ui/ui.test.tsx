/**
 * Tests for the interface primitives.
 *
 * Covers every variant and state of the buttons, fields, alerts, spinner, page, card, link, and
 * the class helper. Accessibility semantics are asserted by role and name, not by markup.
 *
 * @packageDocumentation
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { Alert } from "./Alert";
import { Button } from "./Button";
import { Card } from "./Card";
import { cn } from "./cn";
import { Page } from "./Page";
import { Spinner } from "./Spinner";
import { TextField } from "./TextField";
import { TextLink } from "./TextLink";

describe("cn", () => {
  it("joins truthy strings and drops falsy values", () => {
    expect(cn("a", false, null, undefined, "", "b")).toBe("a b");
    expect(cn()).toBe("");
  });
});

describe("Button", () => {
  it("renders a button that calls its handler", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("supports the submit type and every variant", () => {
    render(
      <>
        <Button type="submit">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="danger">Danger</Button>
      </>,
    );

    expect(screen.getByRole("button", { name: "Primary" })).toHaveAttribute("type", "submit");
    expect(screen.getByRole("button", { name: "Secondary" }).className).toContain("border-line");
    expect(screen.getByRole("button", { name: "Danger" }).className).toContain("bg-danger");
  });

  it("is disabled and reports busy while busy, and when disabled", async () => {
    const onClick = vi.fn();
    render(
      <>
        <Button busy onClick={onClick}>
          Working
        </Button>
        <Button disabled onClick={onClick}>
          Off
        </Button>
      </>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Working" }));
    await userEvent.click(screen.getByRole("button", { name: "Off" }));

    expect(screen.getByRole("button", { name: "Working" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Working" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Off" })).toBeDisabled();
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("TextField", () => {
  it("associates the label and forwards the ref and handlers", async () => {
    const ref = createRef<HTMLInputElement>();
    const onChange = vi.fn();
    const onBlur = vi.fn();
    render(
      <TextField
        label="Email"
        name="email"
        type="email"
        autoComplete="email"
        ref={ref}
        onChange={onChange}
        onBlur={onBlur}
      />,
    );
    const input = screen.getByLabelText("Email");

    await userEvent.type(input, "a");
    await userEvent.tab();

    expect(input).toBe(ref.current);
    expect(input).toHaveAttribute("type", "email");
    expect(input).toHaveAttribute("name", "email");
    expect(input).toHaveAttribute("autocomplete", "email");
    expect(onChange).toHaveBeenCalled();
    expect(onBlur).toHaveBeenCalled();
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("describes the input with its hint and error and marks it invalid", () => {
    render(<TextField label="Username" name="username" hint="Letters only." error="Taken." />);
    const input = screen.getByLabelText("Username");

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Letters only. Error: Taken.");
  });

  it("describes the input with a hint alone", () => {
    render(<TextField label="Username" name="username" hint="Letters only." />);

    expect(screen.getByLabelText("Username")).toHaveAccessibleDescription("Letters only.");
  });

  it("defaults to a text input", () => {
    render(<TextField label="Name" name="name" />);

    expect(screen.getByLabelText("Name")).toHaveAttribute("type", "text");
  });
});

describe("Alert", () => {
  it("announces errors with the alert role and the other kinds as status", () => {
    render(
      <>
        <Alert kind="error" title="Failed">
          Details.
        </Alert>
        <Alert kind="warning">Careful.</Alert>
        <Alert kind="success" title="Done" />
        <Alert kind="info">Note.</Alert>
      </>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
    expect(screen.getByRole("alert")).toHaveTextContent("Details.");
    expect(screen.getAllByRole("status")).toHaveLength(3);
    expect(screen.getByText("Done")).toBeInTheDocument();
  });
});

describe("Spinner", () => {
  it("shows its label as a status", () => {
    render(<Spinner label="Loading" />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });
});

describe("Page and Card", () => {
  it("renders one level-one heading and sets the document title", () => {
    render(
      <Page title="Settings">
        <Card>Body</Card>
      </Page>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Body")).toBeInTheDocument();
    expect(document.title).toBe("Settings · LocalForge");
  });
});

describe("TextLink", () => {
  it("renders an anchor to the route", () => {
    render(
      <MemoryRouter>
        <TextLink to="/account">Account</TextLink>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  });
});
