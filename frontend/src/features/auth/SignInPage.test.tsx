/**
 * Tests for the sign-in page.
 *
 * Covers client validation, a successful sign-in through the CSRF flow, field and form-level
 * backend errors, and a visible outcome for every documented status of the login operation.
 *
 * @packageDocumentation
 */

import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  disconnect,
  envelope,
  fail,
  handler,
  profile,
  seedCsrfCookie,
  server,
} from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

function signedOutStart(): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: "/sign-in" });
}

async function fillAndSubmit(
  user: ReturnType<typeof renderApp>["user"],
  username = "ada",
  password = "correct horse",
): Promise<void> {
  await user.type(await screen.findByLabelText("Username"), username);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("SignInPage", () => {
  it("renders the form and the links to the other account flows", async () => {
    signedOutStart();

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    for (const name of [
      "Forgot your password?",
      "Forgot your username?",
      "Create an account",
      "Resend the activation email",
    ]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("explains missing fields without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operations.browser_login.path, () => {
        requests += 1;
        return HttpResponse.json(profile());
      }),
    );
    const { user } = signedOutStart();

    await user.click(await screen.findByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter your username.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("signs in and lands on the account page with the session established", async () => {
    let body: unknown;
    let csrf: string | null = null;
    server.use(
      http.post(operations.browser_login.path, async ({ request }) => {
        body = await request.clone().json();
        csrf = request.headers.get("X-CSRFToken");
        return HttpResponse.json(profile());
      }),
    );
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
    expect(body).toEqual({ username: "ada", password: "correct horse" });
    expect(csrf).toBe("seeded-token");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("reports a wrong credential generically and keeps what was typed", async () => {
    fail("browser_login", 401, envelope("invalid_credentials", "The credentials are incorrect."));
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The credentials are incorrect.");
    expect(alert).not.toHaveTextContent(/no such|unknown|not found|exists/i);
    expect(screen.getByLabelText("Username")).toHaveValue("ada");
  });

  it("attaches backend field errors to their inputs and lists unmatched ones", async () => {
    fail(
      "browser_login",
      400,
      envelope("validation_error", "Fix the form.", {
        username: ["Too long."],
        password: ["Required."],
        non_field_errors: ["Something general."],
      }),
    );
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    await waitFor(() => {
      expect(screen.getByLabelText("Username")).toHaveAccessibleDescription("Error: Too long.");
      expect(screen.getByLabelText("Password")).toHaveAccessibleDescription("Error: Required.");
    });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Something general.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Username")).toHaveFocus();
    });
  });

  it("shows how long to wait when throttled", async () => {
    fail("browser_login", 429, envelope("throttled", "Too many requests."), {
      "Retry-After": "30",
    });
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    const alert = await screen.findByText("Too many attempts");
    expect(alert.parentElement).toHaveTextContent("Try again in 30 seconds.");
  });

  it("keeps the typed values and says so when offline", async () => {
    disconnect("browser_login");
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveValue("ada");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("uses fixed wording and a reference for a server failure", async () => {
    fail("browser_login", 503, envelope("service_unavailable", "database exploded"));
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The service is unavailable right now.");
    expect(alert).not.toHaveTextContent("database exploded");
    expect(alert).toHaveTextContent("Reference: 00000000-0000-4000-8000-000000000000");
  });

  it("redirects an already signed-in visitor to the account page", async () => {
    server.use(handler("user_profile_retrieve", 200, profile()));
    renderApp({ path: "/sign-in" });

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });

  const statuses = operations.browser_login.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail("browser_login", status, envelope(`code_${String(status)}`, `Message ${String(status)}.`));
    const { user } = signedOutStart();

    await fillAndSubmit(user);

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });
});
