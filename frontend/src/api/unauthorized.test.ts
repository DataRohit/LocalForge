/**
 * Tests for the session-loss signal.
 *
 * Covers notification, unsubscription, and listeners that unsubscribe while being notified.
 * The session layer depends on every listener running exactly once per notification.
 *
 * @packageDocumentation
 */

import { describe, expect, it, vi } from "vitest";

import { notifyUnauthorized, subscribeUnauthorized } from "./unauthorized";

describe("session-loss signal", () => {
  it("calls every listener on each notification", () => {
    const first = vi.fn();
    const second = vi.fn();
    const stopFirst = subscribeUnauthorized(first);
    const stopSecond = subscribeUnauthorized(second);

    notifyUnauthorized();
    notifyUnauthorized();

    expect(first).toHaveBeenCalledTimes(2);
    expect(second).toHaveBeenCalledTimes(2);
    stopFirst();
    stopSecond();
  });

  it("stops calling a listener after it unsubscribes", () => {
    const listener = vi.fn();
    const stop = subscribeUnauthorized(listener);
    stop();

    notifyUnauthorized();

    expect(listener).not.toHaveBeenCalled();
  });

  it("tolerates a listener that unsubscribes during notification", () => {
    const later = vi.fn();
    const stopLater = subscribeUnauthorized(later);
    const stopSelf = subscribeUnauthorized(() => {
      stopSelf();
      stopLater();
    });

    notifyUnauthorized();

    expect(later).toHaveBeenCalledTimes(1);
  });
});
