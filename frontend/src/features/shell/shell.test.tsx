/**
 * Tests for the application shell and its screens.
 *
 * Covers capability-driven navigation, sign-out, the offline and empty states, skip link and
 * focus handling, the session error screen with retry, and the not-found and no-access screens.
 *
 * @packageDocumentation
 */

import { act, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { envelope, fail, profile, server, succeed } from "../../../test/backend";
import { setCookies } from "../../../test/cookies";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

function signedOut(): void {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
}

describe("navigation", () => {
  it("lists only the destinations the granted capabilities allow", async () => {
    succeed("user_profile_retrieve", 200, profile({ capabilities: ["account:read"] }));
    renderApp({ path: "/account" });

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    expect(await within(nav).findByRole("link", { name: "Account" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).queryByRole("link", { name: "Password" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Notifications" })).not.toBeInTheDocument();
  });

  it("lists every destination for a full-capability account", async () => {
    succeed("user_profile_retrieve", 200, profile());
    renderApp({ path: "/account" });

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    for (const name of ["Account", "Password", "Username", "Notifications"]) {
      expect(await within(nav).findByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("offers sign in and register to a signed-out visitor", async () => {
    signedOut();
    renderApp({ path: "/nowhere" });

    const nav = await screen.findByRole("navigation", { name: "Primary" });

    expect(await within(nav).findByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
    expect(within(nav).getByRole("link", { name: "Register" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("shows an empty state when the account has no capabilities", async () => {
    succeed("user_profile_retrieve", 200, profile({ capabilities: [] }));
    renderApp({ path: "/account" });

    expect(
      await screen.findByText("No features are available for this account."),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Not available" })).toBeInTheDocument();
  });
});

describe("sign out", () => {
  it("ends the session and returns to sign-in", async () => {
    setCookies("__Host-lf-csrf=token");
    succeed("user_profile_retrieve", 200, profile());
    succeed("browser_logout", 204);
    const { user } = renderApp({ path: "/account" });

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("stays signed in and explains when sign-out cannot reach the server", async () => {
    setCookies("__Host-lf-csrf=token");
    succeed("user_profile_retrieve", 200, profile());
    server.use(http.post(operations.browser_logout.path, () => HttpResponse.error()));
    const { user } = renderApp({ path: "/account" });

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByText("Sign-out did not complete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });
});

describe("offline notice", () => {
  it("appears when the browser goes offline and disappears when it returns", async () => {
    succeed("user_profile_retrieve", 200, profile());
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    renderApp({ path: "/account" });
    await screen.findByRole("heading", { name: "Your account" });
    expect(screen.queryByText("You are offline")).not.toBeInTheDocument();

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByText("You are offline")).toBeInTheDocument();

    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByText("You are offline")).not.toBeInTheDocument();
  });
});

describe("focus handling", () => {
  it("moves focus to the main region from the skip link", async () => {
    signedOut();
    const { user } = renderApp({ path: "/nowhere" });
    await screen.findByRole("heading", { name: "Page not found" });

    await user.click(screen.getByRole("link", { name: "Skip to main content" }));

    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("moves focus to the main region after a route change but not on first render", async () => {
    signedOut();
    const { user } = renderApp({ path: "/nowhere" });
    await screen.findByRole("heading", { name: "Page not found" });
    expect(screen.getByRole("main")).not.toHaveFocus();

    await user.click(screen.getByRole("link", { name: "Go to the start page" }));

    expect(screen.getByRole("main")).toHaveFocus();
  });
});

describe("session error screen", () => {
  it("explains an offline start and retries", async () => {
    server.use(http.get(operations.user_profile_retrieve.path, () => HttpResponse.error()));
    const { user } = renderApp({ path: "/account" });

    expect(
      await screen.findByRole("heading", { name: "Cannot load your session" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("offline");

    server.resetHandlers();
    succeed("user_profile_retrieve", 200, profile());
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });

  it("uses the error tone for a server failure", async () => {
    fail("user_profile_retrieve", 503, envelope("service_unavailable", "Down."));
    renderApp({ path: "/account" });

    expect(await screen.findByRole("alert")).toHaveTextContent("The service is unavailable");
  });
});

describe("unknown routes", () => {
  it("shows the not-found screen with a way home", async () => {
    signedOut();
    renderApp({ path: "/definitely/not/a/route" });

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the start page" })).toHaveAttribute("href", "/");
  });
});
