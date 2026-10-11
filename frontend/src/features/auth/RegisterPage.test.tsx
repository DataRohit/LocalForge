/**
 * Tests for the registration page.
 *
 * Covers client validation, the neutral confirmation, backend field errors, throttling, being
 * offline, and a visible outcome for every documented status of the registration operation.
 *
 * @packageDocumentation
 */

import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, handler, seedCsrfCookie, server } from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

const ACCEPTED = { username: "ada", email: "ada@example.test" };

function start(): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: "/register" });
}

async function fill(
  user: ReturnType<typeof renderApp>["user"],
  values: { password?: string; confirm?: string } = {},
): Promise<void> {
  const password = values.password ?? "correct horse battery";
  await user.type(await screen.findByLabelText("Username"), "ada");
  await user.type(screen.getByLabelText("Email"), "ada@example.test");
  await user.type(screen.getByLabelText("Password"), password);
  await user.type(screen.getByLabelText("Repeat password"), values.confirm ?? password);
  await user.click(screen.getByRole("button", { name: "Create account" }));
}

describe("RegisterPage", () => {
  it("renders the form and a link back to sign in", async () => {
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Create an account" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Repeat password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("explains every missing field without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_registration_or_activation.path, () => {
        requests += 1;
        return HttpResponse.json(ACCEPTED, { status: 201 });
      }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Create account" }));

    for (const message of [
      "Choose a username.",
      "Enter your email address.",
      "Choose a password.",
      "Repeat the password.",
    ]) {
      expect(await screen.findByText(message)).toBeInTheDocument();
    }
    expect(requests).toBe(0);
  });

  it("refuses mismatched passwords before sending anything", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_registration_or_activation.path, () => {
        requests += 1;
        return HttpResponse.json(ACCEPTED, { status: 201 });
      }),
    );
    const { user } = start();

    await fill(user, { confirm: "something else entirely" });

    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("sends the four fields and then shows a neutral confirmation", async () => {
    let body: unknown;
    server.use(
      http.post(operations.user_registration_or_activation.path, async ({ request }) => {
        body = await request.clone().json();
        return HttpResponse.json(ACCEPTED, { status: 201 });
      }),
    );
    const { user } = start();

    await fill(user);

    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(body).toEqual({
      username: "ada",
      email: "ada@example.test",
      password: "correct horse battery",
      password_confirm: "correct horse battery",
    });
    const note = screen.getByText(/If the details you entered can be used/);
    expect(note).toHaveTextContent("ada@example.test");
    expect(note).not.toHaveTextContent(/has been created|already|taken|exists/i);
    expect(screen.getByRole("link", { name: "Send the activation email again" })).toHaveAttribute(
      "href",
      "/resend-activation",
    );
    expect(screen.getByRole("link", { name: "Back to sign in" })).toBeInTheDocument();
  });

  it("attaches backend field errors to their inputs and lists the rest", async () => {
    fail(
      "user_registration_or_activation",
      400,
      envelope("validation_error", "Fix the form.", {
        email: ["Enter a valid email address."],
        password: ["This password is too common.", "This password is too short."],
        non_field_errors: ["Registration is closed."],
      }),
    );
    const { user } = start();

    await fill(user);

    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
        "Error: Enter a valid email address.",
      );
      expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
        "Error: This password is too common. This password is too short.",
      );
    });
    expect(
      within(await screen.findByRole("alert")).getByText("Registration is closed."),
    ).toBeVisible();
    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveFocus();
    });
    expect(screen.getByLabelText("Username")).toHaveValue("ada");
  });

  it("shows how long to wait when throttled", async () => {
    fail("user_registration_or_activation", 429, envelope("throttled", "Too many requests."), {
      "Retry-After": "45",
    });
    const { user } = start();

    await fill(user);

    expect((await screen.findByText("Too many attempts")).parentElement).toHaveTextContent(
      "Try again in 45 seconds.",
    );
  });

  it("keeps the typed values and says so when offline", async () => {
    disconnect("user_registration_or_activation");
    const { user } = start();

    await fill(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.test");
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  });

  it("treats an accepted response without a body as unexpected", async () => {
    server.use(handler("user_registration_or_activation", 204));
    const { user } = start();

    await fill(user);

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

    await fill(user);

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  });
});
