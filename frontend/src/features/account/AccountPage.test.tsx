/**
 * Tests for the account page.
 *
 * Covers viewing the profile, editing the email, the delete confirmation dialog and its focus
 * handling, capability-dependent sections, the session states, and a visible outcome for every
 * documented status of the update and delete operations.
 *
 * @packageDocumentation
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, profile, server, succeed } from "../../../test/backend";
import { setCookies } from "../../../test/cookies";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

function start(overrides: Parameters<typeof profile>[0] = {}): ReturnType<typeof renderApp> {
  setCookies("__Host-lf-csrf=token");
  succeed("user_profile_retrieve", 200, profile(overrides));
  return renderApp({ path: "/account" });
}

async function saveEmail(
  user: ReturnType<typeof renderApp>["user"],
  email = "grace@example.test",
): Promise<void> {
  const field = await screen.findByLabelText("Email");
  await user.clear(field);
  await user.type(field, email);
  await user.click(screen.getByRole("button", { name: "Save email" }));
}

async function openDialog(user: ReturnType<typeof renderApp>["user"]): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Delete account" }));
  return screen.getByRole("dialog", { name: "Delete your account?" });
}

async function confirmDeletion(
  user: ReturnType<typeof renderApp>["user"],
  dialog: HTMLElement,
): Promise<void> {
  await user.type(within(dialog).getByLabelText("Current password"), "correct horse");
  await user.click(within(dialog).getByRole("button", { name: "Delete my account" }));
}

describe("viewing the profile", () => {
  it("shows the username and email and the editing forms", async () => {
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Your account" }),
    ).toBeInTheDocument();
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.test");
    expect(screen.getByRole("button", { name: "Delete account" })).toBeInTheDocument();
  });

  it("shows a loading state while the profile is read", () => {
    start();

    expect(screen.getByText("Loading your session…")).toBeInTheDocument();
  });

  it("shows the session error with a retry when the profile cannot be read", async () => {
    fail("user_profile_retrieve", 503, envelope("service_unavailable", "Down."));
    const { user } = renderApp({ path: "/account" });

    expect(await screen.findByText("Your session could not be checked.")).toBeInTheDocument();
    succeed("user_profile_retrieve", 200, profile());
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });

  it("explains an account that can neither edit nor delete", async () => {
    start({ capabilities: ["account:read"] });

    expect(
      await screen.findByText("Editing and deleting are not available for this account."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete account" })).not.toBeInTheDocument();
  });

  it("offers only the sections the capabilities allow", async () => {
    start({ capabilities: ["account:read", "account:update"] });
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete account" })).not.toBeInTheDocument();
  });

  it("offers deletion alone when only deletion is granted", async () => {
    start({ capabilities: ["account:read", "account:delete"] });
    expect(await screen.findByRole("button", { name: "Delete account" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });
});

describe("editing the email", () => {
  it("explains an empty address without sending a request", async () => {
    let requests = 0;
    server.use(
      http.patch(operations.user_profile_update.path, () => {
        requests += 1;
        return HttpResponse.json(profile());
      }),
    );
    const { user } = start();
    await user.clear(await screen.findByLabelText("Email"));
    await user.click(screen.getByRole("button", { name: "Save email" }));

    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("saves the address and updates the profile only after the backend accepts it", async () => {
    let body: unknown;
    server.use(
      http.patch(operations.user_profile_update.path, async ({ request }) => {
        body = await request.clone().json();
        return HttpResponse.json(profile({ email: "grace@example.test" }));
      }),
    );
    const { user } = start();

    await saveEmail(user);

    expect(await screen.findByText("Email updated")).toBeInTheDocument();
    expect(body).toEqual({ email: "grace@example.test" });
    expect(screen.getByText("grace@example.test", { selector: "dd" })).toBeInTheDocument();
  });

  it("shows the backend's error on the field and does not change the profile", async () => {
    fail(
      "user_profile_update",
      400,
      envelope("validation_error", "Fix the form.", { email: ["Enter a valid email address."] }),
    );
    const { user } = start();

    await saveEmail(user, "not-an-email");

    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
        "Error: Enter a valid email address.",
      );
    });
    expect(screen.getByText("ada@example.test", { selector: "dd" })).toBeInTheDocument();
    expect(screen.queryByText("Email updated")).not.toBeInTheDocument();
  });

  it("keeps what was typed and says so when offline", async () => {
    disconnect("user_profile_update");
    const { user } = start();

    await saveEmail(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("grace@example.test");
  });

  const statuses = operations.user_profile_update.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_profile_update",
      status,
      envelope(`code_${String(status)}`, `Message ${String(status)}.`),
    );
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));
    const { user } = start();

    await saveEmail(user);

    if (status === 401) {
      expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
      return;
    }
    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Save email" })).toBeEnabled();
  });
});

describe("deleting the account", () => {
  it("asks for confirmation in a dialog and sends nothing yet", async () => {
    let requests = 0;
    server.use(
      http.delete(operations.user_profile_delete.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();

    const dialog = await openDialog(user);

    expect(within(dialog).getByText(/permanently deletes your account/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Current password")).toHaveFocus();
    expect(requests).toBe(0);
  });

  it("cancels without deleting and returns focus to the button that opened it", async () => {
    const { user } = start();
    const dialog = await openDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete account" })).toHaveFocus();
  });

  it("cancels on Escape", async () => {
    const { user } = start();
    const dialog = await openDialog(user);

    fireEvent(dialog, new Event("cancel", { cancelable: true }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete account" })).toHaveFocus();
  });

  it("requires the current password before sending anything", async () => {
    let requests = 0;
    server.use(
      http.delete(operations.user_profile_delete.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();
    const dialog = await openDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Delete my account" }));

    expect(await within(dialog).findByText("Enter your current password.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("deletes the account, signs out, clears cached data, and explains on sign-in", async () => {
    let body: unknown;
    server.use(
      http.delete(operations.user_profile_delete.path, async ({ request }) => {
        body = await request.clone().json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user, client } = start();
    await screen.findByRole("heading", { name: "Your account" });
    client.setQueryData(["other"], "cached");
    const dialog = await openDialog(user);

    await confirmDeletion(user, dialog);

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Account deleted")).toBeInTheDocument();
    expect(body).toEqual({ current_password: "correct horse" });
    expect(client.getQueryData(["other"])).toBeUndefined();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("shows a wrong password on its field and keeps the dialog and the session", async () => {
    fail(
      "user_profile_delete",
      400,
      envelope("validation_error", "Fix the form.", {
        current_password: ["The current password is incorrect."],
      }),
    );
    const { user } = start();
    const dialog = await openDialog(user);

    await confirmDeletion(user, dialog);

    await waitFor(() => {
      expect(within(dialog).getByLabelText("Current password")).toHaveAccessibleDescription(
        "Error: The current password is incorrect.",
      );
    });
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("says so when offline and keeps the account", async () => {
    disconnect("user_profile_delete");
    const { user } = start();
    const dialog = await openDialog(user);

    await confirmDeletion(user, dialog);

    expect(await within(dialog).findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  const statuses = operations.user_profile_delete.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_profile_delete",
      status,
      envelope(`code_${String(status)}`, `Message ${String(status)}.`),
    );
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));
    const { user } = start();
    const dialog = await openDialog(user);

    await confirmDeletion(user, dialog);

    if (status === 401) {
      expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
      return;
    }
    const alert = await within(dialog).findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(within(dialog).getByRole("button", { name: "Delete my account" })).toBeEnabled();
  });
});
