/**
 * Tests for the change-password page.
 *
 * Covers validation, the sign-out that follows a change, field errors, being offline, the
 * capability guard, and a visible outcome for every documented status of the operation.
 *
 * @packageDocumentation
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, profile, server, succeed } from "../../../test/backend";
import { setCookies } from "../../../test/cookies";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

function start(overrides: Parameters<typeof profile>[0] = {}): ReturnType<typeof renderApp> {
  setCookies("__Host-lf-csrf=token");
  succeed("user_profile_retrieve", 200, profile(overrides));
  return renderApp({ path: "/account/password" });
}

async function submit(
  user: ReturnType<typeof renderApp>["user"],
  newPassword = "fresh horse battery",
  confirm = newPassword,
): Promise<void> {
  await user.type(await screen.findByLabelText("Current password"), "old password");
  await user.type(screen.getByLabelText("New password"), newPassword);
  await user.type(screen.getByLabelText("Repeat new password"), confirm);
  await user.click(screen.getByRole("button", { name: "Change password" }));
}

describe("ChangePasswordPage", () => {
  it("renders the form with the sign-out warning", async () => {
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Change your password" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toHaveAccessibleDescription(
      "You will be signed out on every device after the change.",
    );
  });

  it("shows no-access to an account without the credentials capability", async () => {
    start({ capabilities: ["account:read"] });

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
  });

  it("explains empty fields and mismatched passwords without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_password_change.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Enter your current password.")).toBeInTheDocument();
    expect(screen.getByText("Choose a new password.")).toBeInTheDocument();
    expect(screen.getByText("Repeat the new password.")).toBeInTheDocument();

    await submit(user, "one thing", "another thing");
    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("changes the password, ends the local session, and explains on the sign-in page", async () => {
    let body: unknown;
    server.use(
      http.post(operations.user_password_change.path, async ({ request }) => {
        body = await request.clone().json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();

    await submit(user);

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Password changed")).toBeInTheDocument();
    expect(screen.getByText(/signed out everywhere/)).toBeInTheDocument();
    expect(body).toEqual({
      current_password: "old password",
      new_password: "fresh horse battery",
      new_password_confirm: "fresh horse battery",
    });
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("attaches a wrong current password to its field and stays signed in", async () => {
    fail(
      "user_password_change",
      400,
      envelope("validation_error", "Fix the form.", {
        current_password: ["The current password is incorrect."],
      }),
    );
    const { user } = start();

    await submit(user);

    await waitFor(() => {
      expect(screen.getByLabelText("Current password")).toHaveAccessibleDescription(
        "Error: The current password is incorrect.",
      );
    });
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("keeps what was typed and says so when offline", async () => {
    disconnect("user_password_change");
    const { user } = start();

    await submit(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toHaveValue("fresh horse battery");
  });

  const statuses = operations.user_password_change.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_password_change",
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
    expect(screen.getByRole("button", { name: "Change password" })).toBeEnabled();
  });
});
