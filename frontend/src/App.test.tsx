/**
 * Tests for the application root component.
 *
 * Boots the whole application against the mock backend: a signed-in start lands on the account
 * page, a signed-out start does not, and the injected client is used when one is supplied.
 *
 * @packageDocumentation
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { envelope, fail, profile, succeed } from "../test/backend";
import { createTestClient, renderApp } from "../test/render";
import { queryKeys } from "./api";
import { App } from "./App";

describe("App", () => {
  it("lands a signed-in start on the account page with the profile", async () => {
    succeed("user_profile_retrieve", 200, profile());
    renderApp({ path: "/" });

    expect(
      await screen.findByRole("heading", { level: 1, name: "Your account" }),
    ).toBeInTheDocument();
    expect(screen.getByText("ada@example.test")).toBeInTheDocument();
  });

  it("does not show the account page to a signed-out start", async () => {
    fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
    renderApp({ path: "/account" });

    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your account" })).not.toBeInTheDocument();
  });

  it("uses the supplied query client", async () => {
    succeed("user_profile_retrieve", 200, profile());
    const client = createTestClient();
    renderApp({ path: "/account", client });

    await screen.findByRole("heading", { name: "Your account" });

    expect(client.getQueryData(queryKeys.me)).toEqual(profile());
  });

  it("creates its own query client when none is supplied", async () => {
    succeed("user_profile_retrieve", 200, profile());
    render(
      <MemoryRouter initialEntries={["/account"]}>
        <App credentials={null} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });
});
