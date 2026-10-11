/**
 * Fake notification sockets for tests.
 *
 * Replaces the real socket opener with one the test controls: it records every socket opened and
 * lets the test open it, deliver messages, and close it with any code.
 *
 * @packageDocumentation
 */

import type { ConnectNotifications, SocketHandlers } from "../src/api";

/**
 * One fake socket.
 *
 * Exposes the handlers the code under test registered, and records whether the client closed it.
 * Tests call its methods to make the socket open, deliver messages, and close with a code.
 */
export class FakeSocket {
  closedByClient = false;
  readonly #handlers: SocketHandlers;

  /**
   * Create a fake socket.
   *
   * @param handlers - The callbacks the code under test registered.
   */
  constructor(handlers: SocketHandlers) {
    this.#handlers = handlers;
  }

  /**
   * Simulate the connection opening.
   */
  open(): void {
    this.#handlers.onOpen();
  }

  /**
   * Simulate a message arriving.
   *
   * @param data - The message data.
   */
  message(data: unknown): void {
    this.#handlers.onMessage(data);
  }

  /**
   * Simulate the connection closing.
   *
   * @param code - The close code.
   */
  close(code: number): void {
    this.#handlers.onClose(code);
  }
}

/**
 * A fake connect function and the sockets it opened.
 *
 * The function to pass where a connect function is expected, and the list of sockets in order.
 */
export interface FakeConnect {
  readonly connect: ConnectNotifications;
  readonly sockets: FakeSocket[];
}

/**
 * Create a fake connect function.
 *
 * Every call opens a new fake socket that stays silent until the test drives it.
 * The sockets are recorded in order so a test can address the nth connection.
 *
 * @returns The connect function and the sockets it has opened.
 */
export function createFakeConnect(): FakeConnect {
  const sockets: FakeSocket[] = [];
  const connect: ConnectNotifications = (handlers) => {
    const socket = new FakeSocket(handlers);
    sockets.push(socket);
    return {
      close: () => {
        socket.closedByClient = true;
      },
    };
  };
  return { connect, sockets };
}

/**
 * Build a notification text frame.
 *
 * Produces the JSON text a server sends for one notification, so tests do not repeat the envelope.
 * The data defaults to an empty object, which the contract allows.
 *
 * @param event - The event name.
 * @param data - The event data.
 * @returns The JSON text the server sends.
 */
export function notificationFrame(event: string, data: Record<string, unknown> = {}): string {
  return JSON.stringify({ type: "notification", payload: { event, data } });
}
