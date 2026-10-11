/**
 * Tests for the resend-activation page.
 *
 * Covers validation, the state-independent acceptance statement, backend errors, being offline,
 * and a visible outcome for every documented status of the resend operation.
 *
 * @packageDocumentation
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, handler, seedCsrfCookie, server } from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

const STATEMENT = "If the address belongs to an inactive account, a new link is on its way.";

function start(): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: "/resend-activation" });
}

async function submit(
  user: ReturnType<typeof renderApp>["user"],
  email = "ada@example.test",
): Promise<void> {
  await user.type(await screen.findByLabelText("Email"), email);
  await user.click(screen.getByRole("button", { name: "Send activation email" }));
}

describe("ResendActivationPage", () => {
  it("removes the earlier acceptance statement when a later attempt is throttled", async () => {
    server.use(handler("user_activation_resend", 202, { detail: STATEMENT }));
    const { user } = start();
    await submit(user);
    expect(await screen.findByText("Request received")).toBeInTheDocument();

    server.use(
      handler("user_activation_resend", 429, envelope("throttled", "Too many requests."), {
        "Retry-After": "3600",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Send activation email" }));

    expect(await screen.findByText("Too many attempts")).toBeInTheDocument();
    expect(screen.queryByText("Request received")).not.toBeInTheDocument();
  });

  it("renders the form and a link back to sign in", async () => {
    start();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Resend the activation email" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
  });

  it("explains a missing address without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_activation_resend.path, () => {
        requests += 1;
        return HttpResponse.json({ detail: STATEMENT }, { status: 202 });
      }),
    );
    const { user } = start();

    await user.click(await screen.findByRole("button", { name: "Send activation email" }));

    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("shows the backend statement verbatim, whatever address was entered", async () => {
    let body: unknown;
    server.use(
      http.post(operations.user_activation_resend.path, async ({ request }) => {
        body = await request.clone().json();
        return HttpResponse.json({ detail: STATEMENT }, { status: 202 });
      }),
    );
    const { user } = start();

    await submit(user, "nobody@example.test");

    const alert = await screen.findByText("Request received");
    expect(alert.parentElement).toHaveTextContent(STATEMENT);
    expect(body).toEqual({ email: "nobody@example.test" });
  });

  it("attaches a backend field error to the email input", async () => {
    fail(
      "user_activation_resend",
      400,
      envelope("validation_error", "Fix the form.", { email: ["Enter a valid email address."] }),
    );
    const { user } = start();

    await submit(user);

    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
        "Error: Enter a valid email address.",
      );
    });
  });

  it("keeps the typed address and says so when offline", async () => {
    disconnect("user_activation_resend");
    const { user } = start();

    await submit(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.test");
    expect(screen.getByRole("button", { name: "Send activation email" })).toBeEnabled();
  });

  const statuses = operations.user_activation_resend.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(
      "user_activation_resend",
      status,
      envelope(`code_${String(status)}`, `Message ${String(status)}.`),
    );
    const { user } = start();

    await submit(user);

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Send activation email" })).toBeEnabled();
  });
});
