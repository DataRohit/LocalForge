/**
 * Tests for the notifications provider and page.
 *
 * Drive the whole application with a fake socket: connecting only while signed in, the connection
 * notice for every status, the live list and its announcement, session renewal, going offline,
 * sign-out, and the separation between accounts.
 *
 * @packageDocumentation
 */

import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { envelope, fail, profile, server, succeed } from "../../../test/backend";
import { takeConsoleCalls } from "../../../test/console";
import { setCookies } from "../../../test/cookies";
import { renderApp } from "../../../test/render";
import { createFakeConnect, notificationFrame } from "../../../test/socket";
import type { FakeConnect, FakeSocket } from "../../../test/socket";
import { operations } from "../../api/generated/operations";
import { useNotifications } from "./useNotifications";

function start(
  overrides: Parameters<typeof profile>[0] = {},
  path = "/notifications",
): ReturnType<typeof renderApp> & { readonly fake: FakeConnect } {
  setCookies("__Host-lf-csrf=token");
  succeed("user_profile_retrieve", 200, profile(overrides));
  const fake = createFakeConnect();
  return { ...renderApp({ path, connect: fake.connect }), fake };
}

async function firstSocket(fake: FakeConnect): Promise<FakeSocket> {
  await waitFor(() => {
    expect(fake.sockets.length).toBeGreaterThan(0);
  });
  const socket = fake.sockets[0];
  if (socket === undefined) {
    throw new Error("no socket was opened");
  }
  return socket;
}

function drive(action: () => void): void {
  act(action);
}

function feedItems(): HTMLElement[] {
  return within(screen.getByRole("main")).queryAllByRole("listitem");
}

async function expectFeed(texts: readonly string[]): Promise<void> {
  await waitFor(() => {
    const shown = feedItems().map((item) => item.textContent);
    expect(shown).toHaveLength(texts.length);
    texts.forEach((text, index) => {
      expect(shown[index]).toContain(text);
    });
  });
}

describe("connecting", () => {
  it("opens one socket once the signed-in profile is known and says it is connecting", async () => {
    const { fake } = start();

    await screen.findByRole("heading", { level: 1, name: "Notifications" });
    await firstSocket(fake);

    expect(fake.sockets).toHaveLength(1);
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
  });

  it("does not connect for a signed-out visitor", async () => {
    fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));
    const fake = createFakeConnect();
    renderApp({ path: "/sign-in", connect: fake.connect });

    await screen.findByRole("heading", { level: 1, name: "Sign in" });

    expect(fake.sockets).toHaveLength(0);
  });

  it("does not connect for an account that may not receive notifications", async () => {
    const { fake } = start({ capabilities: ["account:read"] });

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();

    expect(fake.sockets).toHaveLength(0);
  });

  it("closes the socket when the page unmounts", async () => {
    const { fake, unmount } = start();
    const socket = await firstSocket(fake);

    unmount();

    expect(socket.closedByClient).toBe(true);
  });
});

describe("the live feed", () => {
  it("shows a live notice and an explicit empty state once open", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);

    drive(() => {
      socket.open();
    });

    expect(await screen.findByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/No notifications yet/)).toBeInTheDocument();
    expect(screen.getByText(/earlier ones are not stored/)).toBeInTheDocument();
  });

  it("lists notifications newest first, announces the latest, and clears the list", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2026, 9, 11, 12, 0, 0));
    const { fake, user } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });

    drive(() => {
      socket.message(notificationFrame("account.updated"));
      socket.message(notificationFrame("account.username_changed"));
    });

    await expectFeed(["Your username was changed.", "Your account was updated."]);
    expect(feedItems()[0]?.querySelector("time")?.getAttribute("datetime")).toBe(
      "2026-10-11T12:00:00.000Z",
    );
    const announcement = document.querySelector('[aria-live="polite"]');
    expect(announcement).toHaveTextContent("Your username was changed.");

    await user.click(screen.getByRole("button", { name: "Clear list" }));

    expect(feedItems()).toHaveLength(0);
    expect(screen.getByText(/No notifications yet/)).toBeInTheDocument();
    now.mockRestore();
  });

  it("ignores malformed frames without breaking the feed", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });

    drive(() => {
      socket.message("{broken");
      socket.message(new ArrayBuffer(1));
      socket.message(JSON.stringify({ type: "error", payload: { code: "permission_denied" } }));
    });

    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(feedItems()).toHaveLength(0);
  });

  it("collects notifications while the person is on another page", async () => {
    const { fake, user } = start({}, "/account");
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
      socket.message(notificationFrame("account.updated"));
    });

    await user.click(await screen.findByRole("link", { name: "Notifications" }));

    await expectFeed(["Your account was updated."]);
  });

  it("describes an event it does not know as plain text", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);

    drive(() => {
      socket.open();
      socket.message(notificationFrame("report.ready"));
    });

    await expectFeed(["Update received: report.ready"]);
  });
});

describe("connection states", () => {
  it("shows reconnecting after a retryable close", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });

    drive(() => {
      socket.close(4500);
    });

    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
  });

  it("stops with the reason and starts again from the button", async () => {
    const { fake, user } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });

    drive(() => {
      socket.close(4404);
    });

    expect(await screen.findByText("Notifications stopped")).toBeInTheDocument();
    expect(screen.getByText(/account is not active/)).toBeInTheDocument();
    expect(fake.sockets).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => {
      expect(fake.sockets).toHaveLength(2);
    });
    expect(screen.getByText("Connecting…")).toBeInTheDocument();
  });

  it("renews the session and reconnects after an expired credential", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });
    server.use(
      http.post(operations.browser_refresh.path, () => new HttpResponse(null, { status: 204 })),
    );

    drive(() => {
      socket.close(4403);
    });

    await waitFor(() => {
      expect(fake.sockets).toHaveLength(2);
    });
  });

  it("returns to sign-in with a notice when the session cannot be renewed", async () => {
    const { fake } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
    });
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));

    drive(() => {
      socket.close(4403);
    });

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Your session ended")).toBeInTheDocument();
  });

  it("pauses when the browser goes offline, keeps what arrived, and resumes online", async () => {
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const { fake } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
      socket.message(notificationFrame("account.updated"));
    });
    await expectFeed(["Your account was updated."]);

    online.mockReturnValue(false);
    drive(() => {
      window.dispatchEvent(new Event("offline"));
    });

    expect(await screen.findByText("Notifications paused")).toBeInTheDocument();
    expect(socket.closedByClient).toBe(true);
    await expectFeed(["Your account was updated."]);

    online.mockReturnValue(true);
    drive(() => {
      window.dispatchEvent(new Event("online"));
    });

    await waitFor(() => {
      expect(fake.sockets).toHaveLength(2);
    });
    await expectFeed(["Your account was updated."]);
  });
});

describe("accounts and sign-out", () => {
  it("closes the socket on sign-out and shows the next account an empty feed", async () => {
    succeed("browser_logout", 204);
    const { fake, user } = start();
    const socket = await firstSocket(fake);
    drive(() => {
      socket.open();
      socket.message(notificationFrame("account.updated"));
    });
    await expectFeed(["Your account was updated."]);

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(socket.closedByClient).toBe(true);

    server.use(
      http.post(operations.browser_login.path, () =>
        HttpResponse.json(
          profile({ id: "99999999-9999-4999-8999-999999999999", username: "grace" }),
        ),
      ),
    );
    await user.type(screen.getByLabelText("Username"), "grace");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.click(await screen.findByRole("link", { name: "Notifications" }));

    expect(await screen.findByText(/No notifications yet/)).toBeInTheDocument();
    expect(feedItems()).toHaveLength(0);
    await waitFor(() => {
      expect(fake.sockets).toHaveLength(2);
    });
  });
});

describe("useNotifications", () => {
  it("throws outside the provider", () => {
    expect(() => renderHook(() => useNotifications())).toThrow(
      "useNotifications needs a NotificationsProvider.",
    );
    takeConsoleCalls();
  });
});
