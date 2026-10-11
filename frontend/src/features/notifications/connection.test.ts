/**
 * Tests for the notification connection controller.
 *
 * Drives the controller with a fake socket and fake timers through every close code, the backoff
 * and jitter, session renewal and its limit, healthy-connection resets, stopping, and late events.
 *
 * @packageDocumentation
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createFakeConnect, notificationFrame } from "../../../test/socket";
import type { FakeConnect, FakeSocket } from "../../../test/socket";
import {
  BASE_DELAY_MS,
  MAX_DELAY_MS,
  MAX_ITEMS,
  MAX_RENEWALS,
  NotificationConnection,
  STABLE_MS,
} from "./connection";
import type { ConnectionState, NotificationItem } from "./connection";
import { THROTTLE_WAIT_MS } from "./closeCodes";

interface Options {
  readonly items?: readonly NotificationItem[];
  readonly renew?: () => Promise<boolean>;
  readonly random?: number;
}

interface Harness {
  readonly connection: NotificationConnection;
  readonly fake: FakeConnect;
  readonly states: ConnectionState[];
  readonly renew: ReturnType<typeof vi.fn<() => Promise<boolean>>>;
  readonly advance: (ms: number) => Promise<void>;
  readonly last: () => ConnectionState;
}

function setup(options: Options = {}): Harness {
  const fake = createFakeConnect();
  const states: ConnectionState[] = [];
  const clock = { now: 1_000_000 };
  const renew = vi.fn(options.renew ?? (() => Promise.resolve(true)));
  const connection = new NotificationConnection(
    {
      connect: fake.connect,
      renew,
      onChange: (state) => {
        states.push(state);
      },
      random: () => options.random ?? 1,
      now: () => clock.now,
    },
    options.items,
  );
  const advance = async (ms: number): Promise<void> => {
    clock.now += ms;
    await vi.advanceTimersByTimeAsync(ms);
  };
  const last = (): ConnectionState => {
    const state = states.at(-1);
    if (state === undefined) {
      throw new Error("no state was reported");
    }
    return state;
  };
  return { connection, fake, states, renew, advance, last };
}

function socketAt(fake: FakeConnect, index: number): FakeSocket {
  const socket = fake.sockets[index];
  if (socket === undefined) {
    throw new Error(`socket ${String(index)} was never opened`);
  }
  return socket;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("opening and receiving", () => {
  it("reports connecting, opens one socket, and reports open when it opens", () => {
    const { connection, fake, states, last } = setup();

    connection.start();

    expect(states[0]).toEqual({ status: "connecting", reason: null, items: [] });
    expect(fake.sockets).toHaveLength(1);
    socketAt(fake, 0).open();
    expect(last()).toEqual({ status: "open", reason: null, items: [] });
  });

  it("lists notifications newest first with increasing ids and the arrival time", async () => {
    const { connection, fake, advance, last } = setup();
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();

    socket.message(notificationFrame("account.updated"));
    await advance(5);
    socket.message(notificationFrame("account.username_changed"));

    expect(last().items).toEqual([
      { id: 2, event: "account.username_changed", receivedAt: 1_000_005 },
      { id: 1, event: "account.updated", receivedAt: 1_000_000 },
    ]);
  });

  it.each([
    ["binary data", new ArrayBuffer(2)],
    ["invalid JSON", "{nope"],
    [
      "an error frame",
      JSON.stringify({ type: "error", payload: { code: "unknown_message_type" } }),
    ],
    ["a wrong envelope", JSON.stringify({ kind: "notification" })],
  ])("ignores %s and keeps the connection open", (_name, data) => {
    const { connection, fake, states, last } = setup();
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();
    const before = states.length;

    socket.message(data);

    expect(states).toHaveLength(before);
    expect(last().status).toBe("open");
    expect(last().items).toEqual([]);
  });

  it("keeps only the newest notifications", () => {
    const { connection, fake, last } = setup();
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();

    for (let index = 0; index < MAX_ITEMS + 10; index += 1) {
      socket.message(notificationFrame(`event.${String(index)}`));
    }

    expect(last().items).toHaveLength(MAX_ITEMS);
    expect(last().items[0]?.event).toBe(`event.${String(MAX_ITEMS + 9)}`);
  });

  it("continues numbering after notifications kept from an earlier connection", () => {
    const kept: NotificationItem[] = [
      { id: 7, event: "account.updated", receivedAt: 1 },
      { id: 3, event: "account.updated", receivedAt: 0 },
    ];
    const { connection, fake, last } = setup({ items: kept });
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();

    socket.message(notificationFrame("account.username_changed"));

    expect(last().items.map((item) => item.id)).toEqual([8, 7, 3]);
  });

  it("forgets its notifications when cleared", () => {
    const { connection, fake, last } = setup();
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();
    socket.message(notificationFrame("account.updated"));

    connection.clear();
    socket.message(notificationFrame("account.username_changed"));

    expect(last().items.map((item) => item.event)).toEqual(["account.username_changed"]);
  });
});

describe("retrying with backoff", () => {
  it.each([4500, 1011, 1012, 1006, 1000, 1001, 3000])(
    "reconnects after a %i close",
    async (code) => {
      const { connection, fake, last, advance } = setup({ random: 1 });
      connection.start();
      socketAt(fake, 0).open();

      socketAt(fake, 0).close(code);

      expect(last()).toEqual({ status: "reconnecting", reason: null, items: [] });
      await advance(BASE_DELAY_MS - 1);
      expect(fake.sockets).toHaveLength(1);
      await advance(1);
      expect(fake.sockets).toHaveLength(2);
      expect(last().status).toBe("reconnecting");
      socketAt(fake, 1).open();
      expect(last().status).toBe("open");
    },
  );

  it("doubles the delay on each failure up to the maximum", async () => {
    const { connection, fake, advance } = setup({ random: 1 });
    connection.start();
    const delays = [1000, 2000, 4000, 8000, 16_000, MAX_DELAY_MS, MAX_DELAY_MS];

    for (const [index, delay] of delays.entries()) {
      socketAt(fake, index).close(1006);
      await advance(delay - 1);
      expect(fake.sockets).toHaveLength(index + 1);
      await advance(1);
      expect(fake.sockets).toHaveLength(index + 2);
    }
  });

  it("applies jitter between half and the whole delay", async () => {
    const low = setup({ random: 0 });
    low.connection.start();
    socketAt(low.fake, 0).close(1006);
    await low.advance(BASE_DELAY_MS / 2 - 1);
    expect(low.fake.sockets).toHaveLength(1);
    await low.advance(1);
    expect(low.fake.sockets).toHaveLength(2);
  });

  it("starts the backoff over after a healthy connection but not after a short one", async () => {
    const { connection, fake, advance } = setup({ random: 1 });
    connection.start();
    socketAt(fake, 0).close(1006);
    await advance(1000);
    socketAt(fake, 1).close(1006);
    await advance(2000);

    socketAt(fake, 2).open();
    await advance(STABLE_MS - 1);
    socketAt(fake, 2).close(1006);
    await advance(3999);
    expect(fake.sockets).toHaveLength(3);
    await advance(1);
    expect(fake.sockets).toHaveLength(4);

    socketAt(fake, 3).open();
    await advance(STABLE_MS);
    socketAt(fake, 3).close(1006);
    await advance(BASE_DELAY_MS - 1);
    expect(fake.sockets).toHaveLength(4);
    await advance(1);
    expect(fake.sockets).toHaveLength(5);
  });

  it("waits out the throttle window after 4408", async () => {
    const { connection, fake, last, advance } = setup();
    connection.start();
    socketAt(fake, 0).open();

    socketAt(fake, 0).close(4408);

    expect(last().status).toBe("reconnecting");
    await advance(THROTTLE_WAIT_MS - 1);
    expect(fake.sockets).toHaveLength(1);
    await advance(1);
    expect(fake.sockets).toHaveLength(2);
  });
});

describe("stopping on codes that say stop", () => {
  it.each([4400, 4404, 4405, 4406, 4407, 1002, 1007, 1009])(
    "stops for %i and never reconnects by itself",
    async (code) => {
      const { connection, fake, last, advance } = setup();
      connection.start();
      socketAt(fake, 0).open();

      socketAt(fake, 0).close(code);
      await advance(10 * MAX_DELAY_MS);

      expect(last().status).toBe("stopped");
      expect(last().reason?.length).toBeGreaterThan(10);
      expect(fake.sockets).toHaveLength(1);
    },
  );
});

describe("renewing the session", () => {
  it.each([4401, 4402, 4403])("renews and reconnects after a %i close", async (code) => {
    const { connection, fake, renew, last, advance } = setup();
    connection.start();
    socketAt(fake, 0).open();

    socketAt(fake, 0).close(code);

    expect(renew).toHaveBeenCalledTimes(1);
    expect(last().status).toBe("reconnecting");
    await advance(0);
    expect(fake.sockets).toHaveLength(2);
  });

  it("stops with a message when the session cannot be renewed", async () => {
    const { connection, fake, last, advance } = setup({ renew: () => Promise.resolve(false) });
    connection.start();
    socketAt(fake, 0).open();

    socketAt(fake, 0).close(4403);
    await advance(MAX_DELAY_MS);

    expect(last()).toMatchObject({
      status: "stopped",
      reason: "Your session ended. Sign in again.",
    });
    expect(fake.sockets).toHaveLength(1);
  });

  it("backs off like any fault when the renewal itself fails", async () => {
    const { connection, fake, last, advance } = setup({
      renew: () => Promise.reject(new Error("offline")),
      random: 1,
    });
    connection.start();
    socketAt(fake, 0).open();

    socketAt(fake, 0).close(4403);
    await advance(0);

    expect(last().status).toBe("reconnecting");
    expect(fake.sockets).toHaveLength(1);
    await advance(BASE_DELAY_MS);
    expect(fake.sockets).toHaveLength(2);
  });

  it("stops instead of renewing forever when connections keep being refused", async () => {
    const { connection, fake, renew, last, advance } = setup();
    connection.start();

    for (let round = 0; round < MAX_RENEWALS; round += 1) {
      socketAt(fake, round).close(4403);
      await advance(0);
    }
    socketAt(fake, MAX_RENEWALS).close(4403);

    expect(renew).toHaveBeenCalledTimes(MAX_RENEWALS);
    expect(last()).toMatchObject({
      status: "stopped",
      reason: "The notification connection could not be kept. Try again.",
    });
    await advance(MAX_DELAY_MS);
    expect(fake.sockets).toHaveLength(MAX_RENEWALS + 1);
  });

  it("allows renewal again after a healthy connection", async () => {
    const { connection, fake, renew, last, advance } = setup();
    connection.start();
    for (let round = 0; round < MAX_RENEWALS; round += 1) {
      socketAt(fake, round).close(4403);
      await advance(0);
    }
    socketAt(fake, MAX_RENEWALS).open();
    await advance(STABLE_MS);

    socketAt(fake, MAX_RENEWALS).close(4403);
    await advance(0);

    expect(renew).toHaveBeenCalledTimes(MAX_RENEWALS + 1);
    expect(last().status).toBe("reconnecting");
    expect(fake.sockets).toHaveLength(MAX_RENEWALS + 2);
  });
});

describe("stopping and late events", () => {
  it("closes the socket and ignores everything it says afterwards", () => {
    const { connection, fake, states } = setup();
    connection.start();
    const socket = socketAt(fake, 0);
    socket.open();
    const before = states.length;

    connection.stop();
    socket.message(notificationFrame("account.updated"));
    socket.open();
    socket.close(1006);

    expect(socket.closedByClient).toBe(true);
    expect(states).toHaveLength(before);
  });

  it("cancels a pending reconnect", async () => {
    const { connection, fake, advance } = setup();
    connection.start();
    socketAt(fake, 0).close(1006);

    connection.stop();
    await advance(10 * MAX_DELAY_MS);

    expect(fake.sockets).toHaveLength(1);
  });

  it("ignores a renewal that finishes after stopping", async () => {
    let finish: (renewed: boolean) => void = () => undefined;
    const { connection, fake, states, advance } = setup({
      renew: () =>
        new Promise<boolean>((resolve) => {
          finish = resolve;
        }),
    });
    connection.start();
    socketAt(fake, 0).close(4403);
    const before = states.length;

    connection.stop();
    finish(true);
    await advance(MAX_DELAY_MS);

    expect(fake.sockets).toHaveLength(1);
    expect(states).toHaveLength(before);
  });

  it("ignores a failed renewal that finishes after stopping", async () => {
    let fail: (reason: Error) => void = () => undefined;
    const { connection, fake, advance } = setup({
      renew: () =>
        new Promise<boolean>((_resolve, reject) => {
          fail = reject;
        }),
    });
    connection.start();
    socketAt(fake, 0).close(4403);

    connection.stop();
    fail(new Error("late"));
    await advance(MAX_DELAY_MS);

    expect(fake.sockets).toHaveLength(1);
  });

  it("can be stopped twice and before it ever opened", () => {
    const { connection } = setup();

    connection.stop();
    connection.stop();

    expect(true).toBe(true);
  });

  it("ignores events from a socket that has been replaced", async () => {
    const { connection, fake, states, advance } = setup({ random: 1 });
    connection.start();
    const old = socketAt(fake, 0);
    old.close(1006);
    await advance(BASE_DELAY_MS);
    socketAt(fake, 1).open();
    const before = states.length;

    old.message(notificationFrame("account.updated"));
    old.open();
    old.close(1006);
    await advance(MAX_DELAY_MS);

    expect(states).toHaveLength(before);
    expect(fake.sockets).toHaveLength(2);
  });
});
