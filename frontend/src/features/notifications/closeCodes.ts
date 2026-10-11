/**
 * Close code policy.
 *
 * Maps every WebSocket close code the notification contract documents, and the transport codes the
 * server can emit, to the one thing the client does next: renew, retry, wait, or stop.
 *
 * @packageDocumentation
 */

/**
 * Throttle wait.
 *
 * The connection admission window is sixty seconds, so a throttled client waits that long.
 */
export const THROTTLE_WAIT_MS = 60_000;

/**
 * What to do after a close.
 *
 * Renew the session and reconnect, retry with backoff, wait a fixed time, or stop with a message
 * for the person. A stop never reconnects by itself.
 */
export type CloseAction =
  | { readonly kind: "renew" }
  | { readonly kind: "retry" }
  | { readonly kind: "wait"; readonly delayMs: number }
  | { readonly kind: "stop"; readonly message: string };

const PROTOCOL = "The connection was closed because of a protocol error. Reload the page.";

const STOPS: Readonly<Record<number, string>> = {
  4400: PROTOCOL,
  4404: "Your account is not active, so notifications are unavailable.",
  4405: "Your account no longer exists.",
  4406: "This page is not allowed to open the notification connection.",
  4407: PROTOCOL,
  1002: PROTOCOL,
  1007: PROTOCOL,
  1009: PROTOCOL,
};

/**
 * Decide the next step after a close.
 *
 * 4401 to 4403 mean the credential is missing or expired, which renewing replaces, and a browser
 * drops an expired cookie so 4401 is the usual form. 4408 waits out the throttle window, codes
 * that say not to reconnect stop, and every other code retries with bounded backoff.
 *
 * @param code - The WebSocket close code.
 * @returns The action to take.
 */
export function classifyClose(code: number): CloseAction {
  if (code === 4401 || code === 4402 || code === 4403) {
    return { kind: "renew" };
  }
  if (code === 4408) {
    return { kind: "wait", delayMs: THROTTLE_WAIT_MS };
  }
  const message = STOPS[code];
  return message === undefined ? { kind: "retry" } : { kind: "stop", message };
}
