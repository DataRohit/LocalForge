/**
 * Tests for the reset-password and reset-username pages.
 *
 * One table drives both flows. They prove the link credentials never leave the request body, a dead
 * token is explained and forgotten, other failures keep the form, and every documented status of
 * the confirm operation has a visible outcome.
 *
 * @packageDocumentation
 */

import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { disconnect, envelope, fail, handler, seedCsrfCookie, server } from "../../../test/backend";
import { renderApp } from "../../../test/render";
import { operations } from "../../api/generated/operations";

const CREDENTIALS = { account: "33333333-3333-4333-8333-333333333333", token: "reset-token-value" };

interface Flow {
  readonly name: string;
  readonly path: string;
  readonly operation: "user_password_reset_confirm" | "user_username_reset_confirm";
  readonly heading: string;
  readonly submitLabel: string;
  readonly family: "password" | "username";
  readonly doneHeading: string;
  readonly requestLink: string;
  readonly requestHref: string;
  readonly fieldLabel: string;
  readonly fieldKey: string;
  readonly emptyMessages: readonly string[];
  readonly fill: (user: ReturnType<typeof renderApp>["user"]) => Promise<void>;
  readonly body: unknown;
}

const PASSWORD_FLOW: Flow = {
  name: "reset password",
  path: "/reset-password",
  operation: "user_password_reset_confirm",
  heading: "Choose a new password",
  submitLabel: "Change password",
  family: "password",
  doneHeading: "Password changed",
  requestLink: "Request a new reset link",
  requestHref: "/forgot-password",
  fieldLabel: "New password",
  fieldKey: "new_password",
  emptyMessages: ["Choose a new password.", "Repeat the new password."],
  fill: async (user) => {
    await user.type(await screen.findByLabelText("New password"), "fresh horse battery");
    await user.type(screen.getByLabelText("Repeat new password"), "fresh horse battery");
  },
  body: {
    ...CREDENTIALS,
    new_password: "fresh horse battery",
    new_password_confirm: "fresh horse battery",
  },
};

const USERNAME_FLOW: Flow = {
  name: "reset username",
  path: "/reset-username",
  operation: "user_username_reset_confirm",
  heading: "Choose a new username",
  submitLabel: "Change username",
  family: "username",
  doneHeading: "Username changed",
  requestLink: "Request a new reset link",
  requestHref: "/forgot-username",
  fieldLabel: "New username",
  fieldKey: "new_username",
  emptyMessages: ["Choose a new username."],
  fill: async (user) => {
    await user.type(await screen.findByLabelText("New username"), "grace");
  },
  body: { ...CREDENTIALS, new_username: "grace" },
};

const FLOWS: readonly Flow[] = [PASSWORD_FLOW, USERNAME_FLOW];

function start(
  flow: Flow,
  credentials: typeof CREDENTIALS | null = CREDENTIALS,
): ReturnType<typeof renderApp> {
  fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
  seedCsrfCookie();
  return renderApp({ path: flow.path, credentials });
}

async function submit(flow: Flow, user: ReturnType<typeof renderApp>["user"]): Promise<void> {
  await flow.fill(user);
  await user.click(screen.getByRole("button", { name: flow.submitLabel }));
}

describe.each(FLOWS)("$name page", (flow) => {
  const operation = operations[flow.operation];

  it("renders the form and sends nothing on load", async () => {
    let requests = 0;
    server.use(
      http.post(operation.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    start(flow);

    expect(
      await screen.findByRole("heading", { level: 1, name: flow.heading }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(flow.fieldLabel)).toBeInTheDocument();
    expect(requests).toBe(0);
  });

  it("explains a link without credentials and offers a new one", async () => {
    start(flow, null);

    expect(await screen.findByText("This reset link cannot be used.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: flow.submitLabel })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: flow.requestLink })).toHaveAttribute(
      "href",
      flow.requestHref,
    );
  });

  it("explains empty fields without sending a request", async () => {
    let requests = 0;
    server.use(
      http.post(operation.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start(flow);

    await user.click(await screen.findByRole("button", { name: flow.submitLabel }));

    for (const message of flow.emptyMessages) {
      expect(await screen.findByText(message)).toBeInTheDocument();
    }
    expect(requests).toBe(0);
  });

  it("sends the link credentials in the body only and then confirms", async () => {
    let body: unknown;
    let url = "";
    server.use(
      http.post(operation.path, async ({ request }) => {
        body = await request.clone().json();
        url = request.url;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start(flow);

    await submit(flow, user);

    expect(await screen.findByRole("heading", { name: flow.doneHeading })).toBeInTheDocument();
    expect(body).toEqual(flow.body);
    expect(url).not.toContain(CREDENTIALS.token);
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/sign-in");
  });

  it.each(["expired", "used", "foreign", "malformed"])(
    "explains a %s token and forgets the credentials",
    async (kind) => {
      fail(
        flow.operation,
        400,
        envelope(`${flow.family}_reset_token_${kind}`, "Backend text that must not show."),
      );
      const { user } = start(flow);

      await submit(flow, user);

      expect(
        await screen.findByText(/This link (has expired|was already used|is not valid)/),
      ).toBeInTheDocument();
      expect(screen.queryByText("Backend text that must not show.")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: flow.submitLabel })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: flow.requestLink })).toBeInTheDocument();
    },
  );

  it("keeps the form and the credentials after a field validation failure", async () => {
    fail(
      flow.operation,
      400,
      envelope("validation_error", "Fix the form.", {
        [flow.fieldKey]: ["That value is not allowed."],
      }),
    );
    const { user } = start(flow);

    await submit(flow, user);

    await waitFor(() => {
      expect(screen.getByLabelText(flow.fieldLabel)).toHaveAccessibleDescription(
        "Error: That value is not allowed.",
      );
    });
    server.use(handler(flow.operation, 204));
    await user.click(screen.getByRole("button", { name: flow.submitLabel }));
    expect(await screen.findByRole("heading", { name: flow.doneHeading })).toBeInTheDocument();
  });

  it("keeps the credentials and says so when offline", async () => {
    disconnect(flow.operation);
    const { user } = start(flow);

    await submit(flow, user);

    expect(await screen.findByText("You are offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: flow.submitLabel })).toBeEnabled();
  });

  const statuses = operation.statuses.filter((status) => status >= 400);
  it.each(statuses)("shows a visible, accessible outcome for a %i answer", async (status) => {
    fail(flow.operation, status, envelope(`code_${String(status)}`, `Message ${String(status)}.`));
    const { user } = start(flow);

    await submit(flow, user);

    const alert = await screen.findByRole(status === 429 ? "status" : "alert");
    expect(alert).toBeVisible();
    expect(alert.textContent.length).toBeGreaterThan(10);
    expect(screen.getByRole("button", { name: flow.submitLabel })).toBeEnabled();
  });
});

describe("reset password confirmation", () => {
  it("refuses mismatched passwords before sending anything", async () => {
    let requests = 0;
    server.use(
      http.post(operations.user_password_reset_confirm.path, () => {
        requests += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = start(PASSWORD_FLOW);

    await user.type(await screen.findByLabelText("New password"), "one thing");
    await user.type(screen.getByLabelText("Repeat new password"), "another thing");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(requests).toBe(0);
  });
});
