/**
 * Tests for the route guards.
 *
 * Renders each guard against every session state with stand-in screens, so the redirect targets
 * and the capability check are asserted without depending on the real route table.
 *
 * @packageDocumentation
 */

import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, profile, succeed } from "../../test/backend";
import { renderWithSession } from "../../test/render";
import { HomeRedirect, PublicOnly, RequireCapability } from "./guards";

function signedOut(): void {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
}

function offline(): void {
  disconnect("user_profile_retrieve");
}

function table(path: string): void {
  renderWithSession(
    <Routes>
      <Route index element={<HomeRedirect />} />
      <Route path="/account" element={<p>account screen</p>} />
      <Route path="/sign-in" element={<p>sign-in screen</p>} />
      <Route
        path="/secret"
        element={
          <RequireCapability capability="account:delete">
            <p>secret screen</p>
          </RequireCapability>
        }
      />
      <Route
        path="/public"
        element={
          <PublicOnly>
            <p>public screen</p>
          </PublicOnly>
        }
      />
    </Routes>,
    { path },
  );
}

describe("HomeRedirect", () => {
  it("shows the loading screen first and sends a signed-in account to its page", async () => {
    succeed("user_profile_retrieve", 200, profile());
    table("/");

    expect(screen.getByRole("status")).toHaveTextContent("Loading your session");
    expect(await screen.findByText("account screen")).toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign-in", async () => {
    signedOut();
    table("/");

    expect(await screen.findByText("sign-in screen")).toBeInTheDocument();
  });

  it("shows the session error screen when the session cannot be read", async () => {
    offline();
    table("/");

    expect(
      await screen.findByRole("heading", { name: "Cannot load your session" }),
    ).toBeInTheDocument();
  });
});

describe("RequireCapability", () => {
  it("renders the screen for an account that holds the capability", async () => {
    succeed("user_profile_retrieve", 200, profile());
    table("/secret");

    expect(await screen.findByText("secret screen")).toBeInTheDocument();
  });

  it("shows no-access when the capability is missing", async () => {
    succeed("user_profile_retrieve", 200, profile({ capabilities: ["account:read"] }));
    table("/secret");

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
    expect(screen.queryByText("secret screen")).not.toBeInTheDocument();
  });

  it("redirects a signed-out visitor to sign-in", async () => {
    signedOut();
    table("/secret");

    expect(await screen.findByText("sign-in screen")).toBeInTheDocument();
  });

  it("waits while loading and shows the error screen when the session fails", async () => {
    offline();
    table("/secret");

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Cannot load your session" }),
    ).toBeInTheDocument();
  });
});

describe("PublicOnly", () => {
  it("renders for a signed-out visitor", async () => {
    signedOut();
    table("/public");

    expect(await screen.findByText("public screen")).toBeInTheDocument();
  });

  it("redirects a signed-in account to its page", async () => {
    succeed("user_profile_retrieve", 200, profile());
    table("/public");

    expect(await screen.findByText("account screen")).toBeInTheDocument();
  });

  it("still renders while the session loads or cannot be read, so offline visitors can use it", async () => {
    offline();
    table("/public");

    expect(screen.getByText("public screen")).toBeInTheDocument();
    await screen.findByText("public screen");
  });
});
