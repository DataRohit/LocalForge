/**
 * Tests for the activation page.
 *
 * Proves the single-use token is sent only after an explicit click, only in the request body, and
 * is forgotten when it cannot be reused. Also covers every documented status of the operation.
 *
 * @packageDocumentation
 */

import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, handler, seedCsrfCookie, server } from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

const CREDENTIALS = { account: "22222222-2222-4222-8222-222222222222", token: "single-use-token" };

function start(credentials: typeof CREDENTIALS | null = CREDENTIALS): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: "/activate", credentials });
}

describe("ActivatePage", () => {
  it("asks for confirmation and sends nothing until the person clicks", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_registration_or_activation.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Activate your account" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate account" })).toBeEnabled();
    expect(requests).toBe(0);
  });

  it("sends the account and token in the body only, then confirms", async () => {
    let body: unknown;
    let url = "";
    server.use(
      http.post(operations.user_registration_or_activation.path, async ({ request }) => {
        body = await request.clone().json();
        url = request.url;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Activate account" }));

    expect(await screen.findByRole("heading", { name: "Account activated" })).toBeInTheDocument();
    expect(body).toEqual(CREDENTIALS);
    expect(url).not.toContain("single-use-token");
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/sign-in");
  });

  it.each([
    ["activation_token_expired", "This activation link has expired."],
    ["activation_token_used", "This activation link was already used."],
    ["activation_token_foreign", "This activation link is not valid."],
    ["activation_token_malformed", "This activation link is not valid."],
  ])("explains the %s failure and forgets the credentials", async (code, message) => {
    fail("user_registration_or_activation", 400, envelope(code, "Backend text."));
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Activate account" }));

    expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resend the activation email" })).toBeInTheDocument();
  });

  it("keeps the credentials and allows another try after a transient failure", async () => {
    disconnect("user_registration_or_activation");
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Activate account" }));

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate account" })).toBeEnabled();

    server.use(handler("user_registration_or_activation", 204));
    await user.click(screen.getByRole("button", { name: "Activate account" }));

    expect(await screen.findByRole("heading", { name: "Account activated" })).toBeInTheDocument();
  });

  it("explains that a link without credentials cannot be used", async () => {
    start(null);

    expect(await screen.findByText("This activation link cannot be used.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resend the activation email" })).toHaveAttribute(
      "href",
      "/resend-activation",
    );
  });

  it("treats a response that carries a body as unexpected", async () => {
    server.use(
      handler("user_registration_or_activation", 201, { username: "ada", email: "a@example.test" }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Activate account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Something unexpected happened");
  });

  const statuses = operations.user_registration_or_activation.statuses.filter(
    (status) => status >= 400,
  );
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_registration_or_activation",
      status,
      envelope(`code_${String(status)}`, `Message ${String(status)}.`),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Activate account" }));

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Activate account" })).toBeEnabled();
  });
});
