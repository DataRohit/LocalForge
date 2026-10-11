/**
 * Tests for the change-username page.
 *
 * Covers validation, the refreshed profile after a change, field errors, being offline, and a
 * visible outcome for every documented status of the operation.
 *
 * @packageDocumentation
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  disconnect,
  envelope,
  fail,
  profile,
  sequence,
  server,
  succeed,
} from "../../../test/backend";
import { setCookies } from "../../../test/cookies";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

function start(): ReturnType<typeof renderApp> {
  setCookies("__Host-lf-csrf=token");
  succeed("user_profile_retrieve", 200, profile());
  return renderApp({ path: "/account/username" });
}

async function submit(user: ReturnType<typeof renderApp>["user"]): Promise<void> {
  await user.type(await screen.findByLabelText("New username"), "grace");
  await user.type(screen.getByLabelText("Current password"), "correct horse");
  await user.click(screen.getByRole("button", { name: "Change username" }));
}

describe("ChangeUsernamePage", () => {
  it("shows the current username", async () => {
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Change your username" }),
    ).toBeInTheDocument();
    expect(screen.getByText("ada")).toBeInTheDocument();
  });

  it("explains empty fields without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_username_change.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Change username" }));

    expect(await screen.findByText("Choose a new username.")).toBeInTheDocument();
    expect(screen.getByText("Enter your current password.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("changes the username, refreshes the profile, and clears the password", async () => {
    setCookies("__Host-lf-csrf=token");
    sequence("user_profile_retrieve", [
      { status: 200, body: profile() },
      { status: 200, body: profile({ username: "grace" }) },
    ]);
    let body: unknown;
    server.use(
      http.post(operations.user_username_change.path, async ({ request }) => {
        body = await request.clone().json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = renderApp({ path: "/account/username" });

    await submit(user);

    expect(await screen.findByText("Username changed")).toBeInTheDocument();
    expect(screen.getByText(/Your username is now grace/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("grace", { selector: "strong" })).toBeInTheDocument();
    });
    expect(body).toEqual({ current_password: "correct horse", new_username: "grace" });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New username")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("attaches a taken username to its field", async () => {
    fail(
      "user_username_change",
      400,
      envelope("validation_error", "Fix the form.", { new_username: ["Choose another username."] }),
    );
    const { user } = start();

    await submit(user);

    await waitFor(() => {
      expect(screen.getByLabelText("New username")).toHaveAccessibleDescription(
        "Error: Choose another username.",
      );
    });
    expect(screen.queryByText("Username changed")).not.toBeInTheDocument();
  });

  it("keeps what was typed and says so when offline", async () => {
    disconnect("user_username_change");
    const { user } = start();

    await submit(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("New username")).toHaveValue("grace");
  });

  const statuses = operations.user_username_change.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_username_change",
      status,
      envelope(`code_${String(status)}`, `Message ${String(status)}.`),
    );
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));
    const { user } = start();

    await submit(user);

    if (status === 401) {
      expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
      expect(screen.getByText("Your session ended")).toBeInTheDocument();
      return;
    }
    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Change username" })).toBeEnabled();
  });
});
