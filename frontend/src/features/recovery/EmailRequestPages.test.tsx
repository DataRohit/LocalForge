/**
 * Tests for the forgot-password and forgot-username pages.
 *
 * Both pages share one screen, so one table drives them and proves they behave identically:
 * validation, the verbatim acceptance statement, backend errors, and every documented status.
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
  handler,
  profile,
  seedCsrfCookie,
  server,
} from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

interface Flow {
  readonly name: string;
  readonly path: string;
  readonly operation: "user_password_reset_request" | "user_username_reset_request";
  readonly heading: string;
}

const FLOWS: readonly Flow[] = [
  {
    name: "forgot password",
    path: "/forgot-password",
    operation: "user_password_reset_request",
    heading: "Forgot your password?",
  },
  {
    name: "forgot username",
    path: "/forgot-username",
    operation: "user_username_reset_request",
    heading: "Forgot your username?",
  },
];

const STATEMENT = "If the address belongs to an active account, a link is on its way.";

function start(flow: Flow): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: flow.path });
}

async function submit(
  user: ReturnType<typeof renderApp>["user"],
  email = "ada@example.test",
): Promise<void> {
  await user.type(await screen.findByLabelText("Email"), email);
  await user.click(screen.getByRole("button", { name: "Send reset link" }));
}

describe.each(FLOWS)("$name page", (flow) => {
  const operation = operations[flow.operation];

  it("renders the form and a link back to sign in", async () => {
    start(flow);

    expect(
      await screen.findByRole("heading", { level: 1, name: flow.heading }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
  });

  it("explains a missing address without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operation.path, () => {
        requests += 1;
        return HttpResponse.json({ detail: STATEMENT }, { status: 202 });
      }),
    );
    const { user } = start(flow);

    await user.click(await screen.findByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("shows the backend statement verbatim whatever address was entered", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(operation.path, async ({ request }) => {
        bodies.push(await request.clone().json());
        return HttpResponse.json({ detail: STATEMENT }, { status: 202 });
      }),
    );
    const { user } = start(flow);

    await submit(user, "nobody@example.test");

    const alert = await screen.findByText("Request received");
    expect(alert.parentElement).toHaveTextContent(STATEMENT);
    expect(bodies).toEqual([{ email: "nobody@example.test" }]);
  });

  it("attaches a backend field error to the email input and keeps the address", async () => {
    fail(
      flow.operation,
      400,
      envelope("validation_error", "Fix the form.", { email: ["Enter a valid email address."] }),
    );
    const { user } = start(flow);

    await submit(user);

    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(
        "Error: Enter a valid email address.",
      );
    });
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.test");
  });

  it("keeps the typed address and says so when offline", async () => {
    disconnect(flow.operation);
    const { user } = start(flow);

    await submit(user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.test");
  });

  it("sends a signed-in visitor to the account page", async () => {
    server.use(handler("user_profile_retrieve", 200, profile()));
    renderApp({ path: flow.path });

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });

  const statuses = operation.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(flow.operation, status, envelope(`code_${String(status)}`, `Message ${String(status)}.`));
    const { user } = start(flow);

    await submit(user);

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: "Send reset link" })).toBeEnabled();
  });
});
