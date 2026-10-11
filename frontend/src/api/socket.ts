/**
 * Notification socket opener.
 *
 * The only code that creates a WebSocket. It connects to the same-origin notification route, where
 * the browser sends the session cookie itself, and reports events through plain callbacks.
 *
 * @packageDocumentation
 */

/**
 * Notification socket route.
 *
 * The authenticated live feed documented in the WebSocket contract.
 */
export const NOTIFICATION_SOCKET_PATH = "/ws/notifications/";

/**
 * Socket event handlers.
 *
 * Called when the socket opens, when a message arrives, and when it closes with its close code.
 */
export interface SocketHandlers {
  readonly onOpen: () => void;
  readonly onMessage: (data: unknown) => void;
  readonly onClose: (code: number) => void;
}

/**
 * An open notification socket.
 *
 * Holds the one operation a caller needs, closing the connection.
 */
export interface NotificationSocket {
  readonly close: () => void;
}

/**
 * Function that opens a notification socket.
 *
 * Features receive this type so tests can supply a fake without a network.
 */
export type ConnectNotifications = (handlers: SocketHandlers) => NotificationSocket;

/**
 * Build the notification socket URL.
 *
 * Uses the page's own host and the matching secure or plain scheme, so one build serves every
 * environment without configuration.
 *
 * @param location - The protocol and host to build from, defaulting to the page location.
 * @returns The absolute WebSocket URL.
 */
export function notificationSocketUrl(
  location: Pick<Location, "protocol" | "host"> = window.location,
): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${NOTIFICATION_SOCKET_PATH}`;
}

/**
 * Open the notification socket.
 *
 * Offers no subprotocol, because the browser authenticates the handshake with the access cookie.
 * Errors need no handler: a socket error is always followed by a close event with its code.
 *
 * @param handlers - Callbacks for open, message, and close.
 * @returns A handle that closes the connection normally.
 */
export const openNotificationSocket: ConnectNotifications = (handlers) => {
  const socket = new WebSocket(notificationSocketUrl());
  socket.addEventListener("open", () => {
    handlers.onOpen();
  });
  socket.addEventListener("message", (event: MessageEvent<unknown>) => {
    handlers.onMessage(event.data);
  });
  socket.addEventListener("close", (event) => {
    handlers.onClose(event.code);
  });
  return {
    close: () => {
      socket.close(1000);
    },
  };
};
