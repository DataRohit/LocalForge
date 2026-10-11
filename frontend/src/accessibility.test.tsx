/**
 * Accessibility audit of every screen.
 *
 * Drives each screen into its initial, loading, empty, error, and populated states against the
 * mock backend and runs axe-core on the result. Contrast and layout are checked in a real browser.
 *
 * @packageDocumentation
 */

import { act, render, screen } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../test/axe";
import { envelope, fail, handler, profile, seedCsrfCookie, server, succeed } from "../test/backend";
import { setCookies } from "../test/cookies";
import { renderApp } from "../test/render";
import { createFakeConnect, notificationFrame } from "../test/socket";
import type { FakeConnect } from "../test/socket";
import { operations } from "./api/generated/operations";
import { notifyUnauthorized } from "./api/unauthorized";

type Rendered = ReturnType<typeof renderApp>;
type Scenario = readonly [name: string, run: () => Promise<void>];

const CREDENTIALS = { account: "44444444-4444-4444-8444-444444444444", token: "single-use-token" };

function signedOut(path: string, credentials: typeof CREDENTIALS | null = null): Rendered {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path, credentials });
}

function signedIn(
  path: string,
  overrides: Parameters<typeof profile>[0] = {},
): Rendered & { readonly fake: FakeConnect } {
  setCookies("__Host-lf-csrf=token");
  succeed("user_profile_retrieve", 200, profile(overrides));
  const fake = createFakeConnect();
  return { ...renderApp({ path, connect: fake.connect }), fake };
}

async function title(name: string): Promise<void> {
  await screen.findByRole("heading", { level: 1, name });
}

async function typeInto(user: Rendered["user"], label: string, text: string): Promise<void> {
  await user.type(await screen.findByLabelText(label), text);
}

async function press(user: Rendered["user"], name: string): Promise<void> {
  await user.click(await screen.findByRole("button", { name }));
}

async function awaitAlert(): Promise<void> {
  await screen.findByRole("alert");
}

function breakIt(id: keyof typeof operations, status = 503): void {
  fail(id, status, envelope("service_unavailable", "The service is unavailable."));
}

async function socketOf(fake: FakeConnect): Promise<FakeConnect["sockets"][number]> {
  await vi.waitFor(() => {
    expect(fake.sockets.length).toBeGreaterThan(0);
  });
  const socket = fake.sockets[0];
  if (socket === undefined) {
    throw new Error("no socket was opened");
  }
  return socket;
}

const shell: Scenario[] = [
  [
    "shell: loading the session",
    async () => {
      server.use(
        http.get(operations.user_profile_retrieve.path, async () => {
          await delay(500);
          return HttpResponse.json(profile());
        }),
      );
      renderApp({ path: "/account" });
      await screen.findByText("Loading your session…");
    },
  ],
  [
    "shell: the session cannot be checked",
    async () => {
      breakIt("user_profile_retrieve");
      renderApp({ path: "/account" });
      await screen.findByText("Your session could not be checked.");
    },
  ],
  [
    "shell: an account with no features",
    async () => {
      signedIn("/account", { capabilities: [] });
      await screen.findByRole("heading", { name: "Not available" });
    },
  ],
  [
    "shell: page not found",
    async () => {
      signedOut("/nowhere");
      await title("Page not found");
    },
  ],
  [
    "shell: offline notice",
    async () => {
      signedOut("/sign-in");
      await title("Sign in");
      vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
      act(() => {
        window.dispatchEvent(new Event("offline"));
      });
      await screen.findByText("You are offline");
    },
  ],
];

const auth: Scenario[] = [
  [
    "sign-in: initial",
    async () => {
      signedOut("/sign-in");
      await title("Sign in");
    },
  ],
  [
    "sign-in: validation errors",
    async () => {
      const { user } = signedOut("/sign-in");
      await press(user, "Sign in");
      await screen.findByText("Enter your username.");
    },
  ],
  [
    "sign-in: server error",
    async () => {
      breakIt("browser_login");
      const { user } = signedOut("/sign-in");
      await typeInto(user, "Username", "ada");
      await typeInto(user, "Password", "secret");
      await press(user, "Sign in");
      await awaitAlert();
    },
  ],
  [
    "sign-in: session ended notice",
    async () => {
      signedIn("/account");
      await title("Your account");
      act(() => {
        notifyUnauthorized();
      });
      await screen.findByText("Your session ended");
    },
  ],
  [
    "register: initial",
    async () => {
      signedOut("/register");
      await title("Create an account");
    },
  ],
  [
    "register: validation errors",
    async () => {
      const { user } = signedOut("/register");
      await press(user, "Create account");
      await screen.findByText("Choose a username.");
    },
  ],
  [
    "register: accepted",
    async () => {
      succeed("user_registration_or_activation", 201, {
        username: "ada",
        email: "ada@example.test",
      });
      const { user } = signedOut("/register");
      await typeInto(user, "Username", "ada");
      await typeInto(user, "Email", "ada@example.test");
      await typeInto(user, "Password", "secret value");
      await typeInto(user, "Repeat password", "secret value");
      await press(user, "Create account");
      await title("Check your email");
    },
  ],
  [
    "activate: ready",
    async () => {
      signedOut("/activate", CREDENTIALS);
      await title("Activate your account");
    },
  ],
  [
    "activate: done",
    async () => {
      server.use(handler("user_registration_or_activation", 204));
      const { user } = signedOut("/activate", CREDENTIALS);
      await press(user, "Activate account");
      await title("Account activated");
    },
  ],
  [
    "activate: no credentials",
    async () => {
      signedOut("/activate");
      await screen.findByText("This activation link cannot be used.");
    },
  ],
  [
    "activate: token failure",
    async () => {
      fail("user_registration_or_activation", 400, envelope("activation_token_expired", "x"));
      const { user } = signedOut("/activate", CREDENTIALS);
      await press(user, "Activate account");
      await screen.findByText(/has expired/);
    },
  ],
  [
    "resend activation: initial",
    async () => {
      signedOut("/resend-activation");
      await title("Resend the activation email");
    },
  ],
  [
    "resend activation: accepted",
    async () => {
      succeed("user_activation_resend", 202, { detail: "A link is on its way." });
      const { user } = signedOut("/resend-activation");
      await typeInto(user, "Email", "ada@example.test");
      await press(user, "Send activation email");
      await screen.findByText("Request received");
    },
  ],
  [
    "resend activation: server error",
    async () => {
      breakIt("user_activation_resend");
      const { user } = signedOut("/resend-activation");
      await typeInto(user, "Email", "ada@example.test");
      await press(user, "Send activation email");
      await awaitAlert();
    },
  ],
];

const recovery: Scenario[] = [
  [
    "forgot password: initial",
    async () => {
      signedOut("/forgot-password");
      await title("Forgot your password?");
    },
  ],
  [
    "forgot password: accepted",
    async () => {
      succeed("user_password_reset_request", 202, { detail: "A link is on its way." });
      const { user } = signedOut("/forgot-password");
      await typeInto(user, "Email", "ada@example.test");
      await press(user, "Send reset link");
      await screen.findByText("Request received");
    },
  ],
  [
    "forgot password: server error",
    async () => {
      breakIt("user_password_reset_request");
      const { user } = signedOut("/forgot-password");
      await typeInto(user, "Email", "ada@example.test");
      await press(user, "Send reset link");
      await awaitAlert();
    },
  ],
  [
    "forgot username: initial",
    async () => {
      signedOut("/forgot-username");
      await title("Forgot your username?");
    },
  ],
  [
    "forgot username: accepted",
    async () => {
      succeed("user_username_reset_request", 202, { detail: "A link is on its way." });
      const { user } = signedOut("/forgot-username");
      await typeInto(user, "Email", "ada@example.test");
      await press(user, "Send reset link");
      await screen.findByText("Request received");
    },
  ],
  [
    "reset password: ready",
    async () => {
      signedOut("/reset-password", CREDENTIALS);
      await title("Choose a new password");
    },
  ],
  [
    "reset password: validation errors",
    async () => {
      const { user } = signedOut("/reset-password", CREDENTIALS);
      await press(user, "Change password");
      await screen.findByText("Choose a new password.");
    },
  ],
  [
    "reset password: done",
    async () => {
      server.use(handler("user_password_reset_confirm", 204));
      const { user } = signedOut("/reset-password", CREDENTIALS);
      await typeInto(user, "New password", "fresh value");
      await typeInto(user, "Repeat new password", "fresh value");
      await press(user, "Change password");
      await title("Password changed");
    },
  ],
  [
    "reset password: dead link",
    async () => {
      signedOut("/reset-password");
      await screen.findByText("This reset link cannot be used.");
    },
  ],
  [
    "reset username: ready",
    async () => {
      signedOut("/reset-username", CREDENTIALS);
      await title("Choose a new username");
    },
  ],
  [
    "reset username: done",
    async () => {
      server.use(handler("user_username_reset_confirm", 204));
      const { user } = signedOut("/reset-username", CREDENTIALS);
      await typeInto(user, "New username", "grace");
      await press(user, "Change username");
      await title("Username changed");
    },
  ],
  [
    "reset username: expired token",
    async () => {
      fail("user_username_reset_confirm", 400, envelope("username_reset_token_expired", "x"));
      const { user } = signedOut("/reset-username", CREDENTIALS);
      await typeInto(user, "New username", "grace");
      await press(user, "Change username");
      await screen.findByText(/has expired/);
    },
  ],
];

const account: Scenario[] = [
  [
    "account: populated",
    async () => {
      signedIn("/account");
      await title("Your account");
    },
  ],
  [
    "account: read only",
    async () => {
      signedIn("/account", { capabilities: ["account:read"] });
      await screen.findByText("Editing and deleting are not available for this account.");
    },
  ],
  [
    "account: email saved",
    async () => {
      succeed("user_profile_update", 200, profile({ email: "grace@example.test" }));
      const { user } = signedIn("/account");
      await title("Your account");
      await user.clear(await screen.findByLabelText("Email"));
      await user.type(screen.getByLabelText("Email"), "grace@example.test");
      await press(user, "Save email");
      await screen.findByText("Email updated");
    },
  ],
  [
    "account: email error",
    async () => {
      breakIt("user_profile_update");
      const { user } = signedIn("/account");
      await title("Your account");
      await press(user, "Save email");
      await awaitAlert();
    },
  ],
  [
    "account: delete dialog open",
    async () => {
      const { user } = signedIn("/account");
      await press(user, "Delete account");
      await screen.findByRole("dialog", { name: "Delete your account?" });
    },
  ],
  [
    "account: delete dialog error",
    async () => {
      breakIt("user_profile_delete");
      const { user } = signedIn("/account");
      await press(user, "Delete account");
      await typeInto(user, "Current password", "secret value");
      await press(user, "Delete my account");
      await awaitAlert();
    },
  ],
  [
    "change password: initial",
    async () => {
      signedIn("/account/password");
      await title("Change your password");
    },
  ],
  [
    "change password: validation errors",
    async () => {
      const { user } = signedIn("/account/password");
      await press(user, "Change password");
      await screen.findByText("Enter your current password.");
    },
  ],
  [
    "change password: server error",
    async () => {
      breakIt("user_password_change");
      const { user } = signedIn("/account/password");
      await typeInto(user, "Current password", "old value");
      await typeInto(user, "New password", "new value");
      await typeInto(user, "Repeat new password", "new value");
      await press(user, "Change password");
      await awaitAlert();
    },
  ],
  [
    "change username: initial",
    async () => {
      signedIn("/account/username");
      await title("Change your username");
    },
  ],
  [
    "change username: changed",
    async () => {
      succeed("user_username_change", 204);
      const { user } = signedIn("/account/username");
      await typeInto(user, "New username", "grace");
      await typeInto(user, "Current password", "secret value");
      await press(user, "Change username");
      await screen.findByText("Username changed");
    },
  ],
  [
    "change username: server error",
    async () => {
      breakIt("user_username_change");
      const { user } = signedIn("/account/username");
      await typeInto(user, "New username", "grace");
      await typeInto(user, "Current password", "secret value");
      await press(user, "Change username");
      await awaitAlert();
    },
  ],
];

const notifications: Scenario[] = [
  [
    "notifications: connecting",
    async () => {
      signedIn("/notifications");
      await screen.findByText("Connecting…");
    },
  ],
  [
    "notifications: live and empty",
    async () => {
      const { fake } = signedIn("/notifications");
      const socket = await socketOf(fake);
      act(() => {
        socket.open();
      });
      await screen.findByText("Live");
    },
  ],
  [
    "notifications: populated",
    async () => {
      const { fake } = signedIn("/notifications");
      const socket = await socketOf(fake);
      act(() => {
        socket.open();
        socket.message(notificationFrame("account.updated"));
        socket.message(notificationFrame("account.username_changed"));
      });
      await screen.findByRole("button", { name: "Clear list" });
    },
  ],
  [
    "notifications: reconnecting",
    async () => {
      const { fake } = signedIn("/notifications");
      const socket = await socketOf(fake);
      act(() => {
        socket.open();
        socket.close(4500);
      });
      await screen.findByText("Reconnecting…");
    },
  ],
  [
    "notifications: stopped",
    async () => {
      const { fake } = signedIn("/notifications");
      const socket = await socketOf(fake);
      act(() => {
        socket.open();
        socket.close(4404);
      });
      await screen.findByText("Notifications stopped");
    },
  ],
  [
    "notifications: offline",
    async () => {
      signedIn("/notifications");
      await title("Notifications");
      vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
      act(() => {
        window.dispatchEvent(new Event("offline"));
      });
      await screen.findByText("Notifications paused");
    },
  ],
];

describe.each([
  ["session and shell", shell],
  ["authentication", auth],
  ["recovery", recovery],
  ["account", account],
  ["notifications", notifications],
] as const)("accessibility: %s", (_group, scenarios) => {
  it.each(scenarios)("%s has no axe violations", async (_name, run) => {
    await run();

    expect(await axeViolations()).toEqual([]);
  });
});

describe("the accessibility check itself", () => {
  it("reports real violations, so a clean result means something", async () => {
    render(
      createElement("main", null, [
        createElement("img", { key: "image", src: "logo.png" }),
        createElement("button", { key: "button" }),
        createElement("input", { key: "input" }),
      ]),
    );

    const found = (await axeViolations()).map((line) => line.split(":")[0]);

    expect(found).toEqual(expect.arrayContaining(["image-alt", "button-name", "label"]));
  });
});

describe("keyboard use", () => {
  function nameOf(element: Element | null): string {
    if (element instanceof HTMLInputElement) {
      return element.labels?.[0]?.textContent ?? "";
    }
    return element?.getAttribute("aria-label") ?? element?.textContent ?? "";
  }

  it("reaches every control of the sign-in page in a logical order and signs in", async () => {
    server.use(handler("browser_login", 200, profile()));
    const { user } = signedOut("/sign-in");
    await title("Sign in");

    const order: string[] = [];
    for (let step = 0; step < 11; step += 1) {
      await user.tab();
      order.push(nameOf(document.activeElement));
    }

    expect(order).toEqual([
      "Skip to main content",
      "LocalForge",
      "Sign in",
      "Register",
      "Username",
      "Password",
      "Sign in",
      "Forgot your password?",
      "Forgot your username?",
      "Create an account",
      "Resend the activation email",
    ]);

    await user.click(screen.getByLabelText("Username"));
    await user.keyboard("ada");
    await user.tab();
    await user.keyboard("correct horse{Enter}");

    await title("Your account");
  });

  it("opens the delete dialog from the keyboard and returns focus when it closes", async () => {
    const { user } = signedIn("/account");
    const opener = await screen.findByRole("button", { name: "Delete account" });
    opener.focus();

    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog", { name: "Delete your account?" })).toBeVisible();
    expect(screen.getByLabelText("Current password")).toHaveFocus();

    await user.keyboard("{Tab}{Tab}{Enter}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
