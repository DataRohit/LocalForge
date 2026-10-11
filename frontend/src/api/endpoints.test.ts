/**
 * Tests for the typed endpoint functions.
 *
 * Confirms each wrapper reaches its documented route with the right method and body, and that the
 * profile guard rejects a body that is not a profile.
 *
 * @packageDocumentation
 */

import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { handler, profile, server } from "../../test/backend";
import { setCookies } from "../../test/cookies";
import {
  activate,
  changePassword,
  changeUsername,
  confirmPasswordReset,
  confirmUsernameReset,
  deleteAccount,
  fetchProfile,
  login,
  logout,
  register,
  requestPasswordReset,
  requestUsernameReset,
  resendActivation,
  seedCsrf,
  updateProfile,
} from "./endpoints";
import { UnexpectedResponseError } from "./errors";
import { operations } from "./generated/operations";

const ACCOUNT = "22222222-2222-4222-8222-222222222222";

beforeEach(() => {
  setCookies("__Host-lf-csrf=token-1");
});

async function bodyOf(request: Request): Promise<unknown> {
  return request.clone().json();
}

describe("profile endpoints", () => {
  it("login posts the credentials and returns the profile", async () => {
    let sent: unknown;
    server.use(
      http.post(operations.browser_login.path, async ({ request }) => {
        sent = await bodyOf(request);
        return HttpResponse.json(profile());
      }),
    );

    await expect(login({ username: "ada", password: "pw" })).resolves.toEqual(profile());
    expect(sent).toEqual({ username: "ada", password: "pw" });
  });

  it("fetchProfile reads the profile and accepts a signal", async () => {
    server.use(handler("user_profile_retrieve", 200, profile()));

    await expect(fetchProfile()).resolves.toEqual(profile());
    await expect(fetchProfile(new AbortController().signal)).resolves.toEqual(profile());
  });

  it("updateProfile patches the email", async () => {
    let method = "";
    let sent: unknown;
    server.use(
      http.patch(operations.user_profile_update.path, async ({ request }) => {
        method = request.method;
        sent = await bodyOf(request);
        return HttpResponse.json(profile({ email: "new@example.test" }));
      }),
    );

    const updated = await updateProfile({ email: "new@example.test" });

    expect(method).toBe("PATCH");
    expect(sent).toEqual({ email: "new@example.test" });
    expect(updated.email).toBe("new@example.test");
  });

  it.each([
    ["a missing field", { id: "x", username: "u", email: "e@example.test" }],
    ["an unknown capability", { ...profile(), capabilities: ["account:read", "root"] }],
    ["an empty body", {}],
  ])("rejects a profile body with %s", async (_name, body) => {
    server.use(handler("user_profile_retrieve", 200, body));

    await expect(fetchProfile()).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

describe("bodiless and acceptance endpoints", () => {
  it("seedCsrf, with and without a signal, and logout resolve to undefined", async () => {
    server.use(handler("browser_csrf", 204), handler("browser_logout", 204));

    await expect(seedCsrf()).resolves.toBeUndefined();
    await expect(seedCsrf(new AbortController().signal)).resolves.toBeUndefined();
    await expect(logout()).resolves.toBeUndefined();
  });

  it("deleteAccount sends the current password", async () => {
    let sent: unknown;
    server.use(
      http.delete(operations.user_profile_delete.path, async ({ request }) => {
        sent = await bodyOf(request);
        return new HttpResponse(null, { status: 204 });
      }),
    );

    await expect(deleteAccount({ current_password: "pw" })).resolves.toBeUndefined();
    expect(sent).toEqual({ current_password: "pw" });
  });

  it("register returns the accepted identifiers and activate returns nothing", async () => {
    server.use(
      handler("user_registration_or_activation", 201, {
        username: "ada",
        email: "ada@example.test",
      }),
    );
    await expect(
      register({
        username: "ada",
        email: "ada@example.test",
        password: "pw",
        password_confirm: "pw",
      }),
    ).resolves.toEqual({ username: "ada", email: "ada@example.test" });

    server.resetHandlers();
    server.use(handler("user_registration_or_activation", 204));
    await expect(activate({ account: ACCOUNT, token: "t" })).resolves.toBeUndefined();
  });

  it("register rejects an accepted response that has no body", async () => {
    server.use(handler("user_registration_or_activation", 204));

    await expect(
      register({ username: "a", email: "a@example.test", password: "p", password_confirm: "p" }),
    ).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it.each([
    [
      "resendActivation",
      "user_activation_resend",
      () => resendActivation({ email: "a@example.test" }),
    ],
    [
      "requestPasswordReset",
      "user_password_reset_request",
      () => requestPasswordReset({ email: "a@example.test" }),
    ],
    [
      "requestUsernameReset",
      "user_username_reset_request",
      () => requestUsernameReset({ email: "a@example.test" }),
    ],
  ] as const)("%s returns the acceptance statement", async (_name, id, run) => {
    server.use(handler(id, 202, { detail: "If the address is eligible, an email was sent." }));

    await expect(run()).resolves.toEqual({
      detail: "If the address is eligible, an email was sent.",
    });
  });

  it.each([
    [
      "confirmPasswordReset",
      "user_password_reset_confirm",
      () =>
        confirmPasswordReset({
          account: ACCOUNT,
          token: "t",
          new_password: "pw",
          new_password_confirm: "pw",
        }),
    ],
    [
      "confirmUsernameReset",
      "user_username_reset_confirm",
      () => confirmUsernameReset({ account: ACCOUNT, token: "t", new_username: "grace" }),
    ],
    [
      "changePassword",
      "user_password_change",
      () => changePassword({ current_password: "a", new_password: "b", new_password_confirm: "b" }),
    ],
    [
      "changeUsername",
      "user_username_change",
      () => changeUsername({ current_password: "a", new_username: "grace" }),
    ],
  ] as const)("%s resolves to undefined on 204", async (_name, id, run) => {
    server.use(handler(id, 204));

    await expect(run()).resolves.toBeUndefined();
  });
});
