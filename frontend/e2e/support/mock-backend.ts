/**
 * Network-level backend mock for the browser audit.
 *
 * Fulfils the profile route and the notification socket inside the browser, so the audit drives
 * the real built application without any backend. Unsafe requests are not mocked: the audit looks
 * at screens and states, not at flows that need the secure cookies.
 *
 * @packageDocumentation
 */

import type { Page } from "@playwright/test";

const ALL_CAPABILITIES = [
  "account:read",
  "account:update",
  "account:delete",
  "credentials:change",
  "notifications:receive",
];

/**
 * Notification frames the mocked socket can send.
 *
 * Each is the JSON text of one documented notification.
 */
export const FRAMES = [
  JSON.stringify({ type: "notification", payload: { event: "account.updated", data: {} } }),
  JSON.stringify({
    type: "notification",
    payload: { event: "account.username_changed", data: {} },
  }),
];

/**
 * Make the profile route answer 401.
 *
 * The application then behaves as a visitor with no session.
 * The body is the documented error envelope, so the client parses it like a real answer.
 *
 * @param page - The page to mock.
 */
export async function mockSignedOut(page: Page): Promise<void> {
  await page.route("**/api/v1/users/me/", (route) =>
    route.fulfill({
      status: 401,
      json: {
        code: "not_authenticated",
        message: "Authentication credentials were not provided.",
        details: {},
        request_id: "00000000-0000-4000-8000-000000000000",
      },
    }),
  );
}

/**
 * Make the profile route return an account and accept the notification socket.
 *
 * The socket is answered by the mock and stays open, optionally delivering frames.
 * Every capability is granted, so each guarded screen is reachable.
 *
 * @param page - The page to mock.
 * @param frames - Notification frames to send as soon as the socket opens.
 */
export async function mockSignedIn(page: Page, frames: readonly string[] = []): Promise<void> {
  await page.route("**/api/v1/users/me/", (route) =>
    route.fulfill({
      status: 200,
      json: {
        id: "11111111-1111-4111-8111-111111111111",
        username: "ada",
        email: "ada@example.test",
        capabilities: ALL_CAPABILITIES,
      },
    }),
  );
  await page.routeWebSocket("**/ws/notifications/", (socket) => {
    for (const frame of frames) {
      socket.send(frame);
    }
  });
}
