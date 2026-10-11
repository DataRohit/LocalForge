/**
 * Tests for the notification socket opener.
 *
 * Covers URL construction for plain and secure pages and the forwarding of open, message, and close
 * events from a stand-in WebSocket, including a normal close from the client.
 *
 * @packageDocumentation
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { NOTIFICATION_SOCKET_PATH, notificationSocketUrl, openNotificationSocket } from "./socket";

class StubSocket extends EventTarget {
  static instances: StubSocket[] = [];
  readonly url: string;
  closedWith: number | null = null;

  constructor(url: string) {
    super();
    this.url = url;
    StubSocket.instances.push(this);
  }

  close(code: number): void {
    this.closedWith = code;
  }
}

afterEach(() => {
  StubSocket.instances = [];
  vi.unstubAllGlobals();
});

describe("notificationSocketUrl", () => {
  it("uses ws for a plain page and wss for a secure page", () => {
    expect(notificationSocketUrl({ protocol: "http:", host: "localforge.localhost:8080" })).toBe(
      "ws://localforge.localhost:8080/ws/notifications/",
    );
    expect(notificationSocketUrl({ protocol: "https:", host: "localforge.datarohit.com" })).toBe(
      "wss://localforge.datarohit.com/ws/notifications/",
    );
  });

  it("defaults to the page location and the documented route", () => {
    expect(notificationSocketUrl()).toBe(`ws://${window.location.host}${NOTIFICATION_SOCKET_PATH}`);
    expect(NOTIFICATION_SOCKET_PATH).toBe("/ws/notifications/");
  });
});

describe("openNotificationSocket", () => {
  it("forwards open, message, and close events and closes normally", () => {
    vi.stubGlobal("WebSocket", StubSocket);
    const onOpen = vi.fn();
    const onMessage = vi.fn();
    const onClose = vi.fn();

    const handle = openNotificationSocket({ onOpen, onMessage, onClose });
    const socket = StubSocket.instances[0];
    socket?.dispatchEvent(new Event("open"));
    socket?.dispatchEvent(new MessageEvent("message", { data: "hello" }));
    socket?.dispatchEvent(new CloseEvent("close", { code: 4403 }));
    handle.close();

    expect(socket?.url).toBe(notificationSocketUrl());
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith("hello");
    expect(onClose).toHaveBeenCalledWith(4403);
    expect(socket?.closedWith).toBe(1000);
  });
});
