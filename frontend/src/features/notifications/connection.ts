/**
 * Notification connection controller.
 *
 * Owns one notification socket and its whole lifecycle: opening it, collecting notifications,
 * and reacting to each close code with a renewal, a bounded backoff, a fixed wait, or a stop.
 * It knows nothing about React, so every path can be driven with a fake socket and fake timers.
 *
 * @packageDocumentation
 */

import type { ConnectNotifications } from "../../api";
import { classifyClose } from "./closeCodes";
import { parseNotification } from "./frame";

/**
 * Smallest reconnect delay.
 *
 * The first retry waits about this long before jitter is applied.
 */
export const BASE_DELAY_MS = 1000;

/**
 * Largest reconnect delay.
 *
 * Backoff doubles up to this bound and then stays there.
 */
export const MAX_DELAY_MS = 30_000;

/**
 * Time a connection must stay open to count as healthy.
 *
 * A healthy connection resets the retry counters, so a flapping connection keeps backing off.
 */
export const STABLE_MS = 10_000;

/**
 * Most consecutive credential renewals without a healthy connection.
 *
 * Beyond this the controller stops instead of renewing forever.
 */
export const MAX_RENEWALS = 3;

/**
 * Most notifications kept.
 *
 * The oldest are dropped first, because the feed is live and has no history.
 */
export const MAX_ITEMS = 50;

const LOST = "The notification connection could not be kept. Try again.";
const ENDED = "Your session ended. Sign in again.";

/**
 * Connection status.
 *
 * Connecting before the first open, open while live, reconnecting between attempts, and stopped
 * once the controller will not try again by itself.
 */
export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "stopped";

/**
 * One received notification.
 *
 * A number unique within the controller, the event name, and the time it arrived.
 */
export interface NotificationItem {
  readonly id: number;
  readonly event: string;
  readonly receivedAt: number;
}

/**
 * Controller state.
 *
 * The status, the reason shown when stopped, and the notifications received, newest first.
 */
export interface ConnectionState {
  readonly status: ConnectionStatus;
  readonly reason: string | null;
  readonly items: readonly NotificationItem[];
}

/**
 * Controller dependencies.
 *
 * The socket opener, the session renewal, the state callback, and the random and clock sources,
 * all supplied by the caller so tests control them.
 */
export interface ConnectionDependencies {
  readonly connect: ConnectNotifications;
  readonly renew: () => Promise<boolean>;
  readonly onChange: (state: ConnectionState) => void;
  readonly random: () => number;
  readonly now: () => number;
}

/**
 * Notification connection.
 *
 * Call start once and stop when the account signs out, the component unmounts, or the browser goes
 * offline. After stop it never touches the network or calls back again.
 */
export class NotificationConnection {
  readonly #deps: ConnectionDependencies;
  #state: ConnectionState;
  #socket: { readonly close: () => void } | null = null;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #epoch = 0;
  #generation = 0;
  #attempt = 0;
  #renewals = 0;
  #openedAt: number | null = null;
  #nextId: number;

  /**
   * Create a controller.
   *
   * Does not open anything until start is called.
   *
   * @param deps - The dependencies.
   * @param items - Notifications to keep from an earlier connection.
   */
  constructor(deps: ConnectionDependencies, items: readonly NotificationItem[] = []) {
    this.#deps = deps;
    this.#state = { status: "connecting", reason: null, items };
    this.#nextId = items.reduce((highest, item) => Math.max(highest, item.id), 0) + 1;
  }

  /**
   * Open the first connection.
   *
   * Reports the connecting state and then opens the socket.
   */
  start(): void {
    this.#emit({ status: "connecting", reason: null });
    this.#open();
  }

  /**
   * Stop for good.
   *
   * Cancels any wait or renewal in flight, closes the socket normally, and ignores every event
   * that arrives afterwards.
   */
  stop(): void {
    this.#epoch += 1;
    this.#generation += 1;
    this.#cancelTimer();
    this.#socket?.close();
    this.#socket = null;
  }

  /**
   * Forget the notifications received so far.
   *
   * The caller shows the empty list itself, so no state is reported.
   */
  clear(): void {
    this.#state = { ...this.#state, items: [] };
  }

  /**
   * Report a state change.
   *
   * @param patch - The fields that changed.
   */
  #emit(patch: Partial<ConnectionState>): void {
    this.#state = { ...this.#state, ...patch };
    this.#deps.onChange(this.#state);
  }

  /**
   * Cancel a pending reconnect timer.
   */
  #cancelTimer(): void {
    if (this.#timer !== null) {
      clearTimeout(this.#timer);
      this.#timer = null;
    }
  }

  /**
   * Open a socket for the current generation.
   *
   * A generation counter lets handlers of an older socket recognise themselves and do nothing.
   */
  #open(): void {
    this.#generation += 1;
    const generation = this.#generation;
    this.#openedAt = null;
    this.#socket = this.#deps.connect({
      onOpen: () => {
        if (generation === this.#generation) {
          this.#openedAt = this.#deps.now();
          this.#emit({ status: "open", reason: null });
        }
      },
      onMessage: (data) => {
        if (generation === this.#generation) {
          this.#receive(data);
        }
      },
      onClose: (code) => {
        if (generation === this.#generation) {
          this.#closed(code);
        }
      },
    });
  }

  /**
   * Record a message when it is a notification.
   *
   * @param data - The message data.
   */
  #receive(data: unknown): void {
    const event = parseNotification(data);
    if (event === null) {
      return;
    }
    const item: NotificationItem = { id: this.#nextId, event, receivedAt: this.#deps.now() };
    this.#nextId += 1;
    this.#emit({ items: [item, ...this.#state.items].slice(0, MAX_ITEMS) });
  }

  /**
   * React to a close.
   *
   * Resets the counters after a healthy connection, then follows the action for the close code.
   *
   * @param code - The close code.
   */
  #closed(code: number): void {
    this.#socket = null;
    if (this.#openedAt !== null && this.#deps.now() - this.#openedAt >= STABLE_MS) {
      this.#attempt = 0;
      this.#renewals = 0;
    }
    this.#openedAt = null;
    const action = classifyClose(code);
    if (action.kind === "stop") {
      this.#emit({ status: "stopped", reason: action.message });
    } else if (action.kind === "wait") {
      this.#schedule(action.delayMs);
    } else if (action.kind === "retry") {
      this.#attempt += 1;
      this.#schedule(this.#backoff());
    } else {
      this.#renew();
    }
  }

  /**
   * Compute the next backoff delay.
   *
   * Doubles from the base up to the maximum and applies jitter between half and the whole value,
   * so many clients do not reconnect at the same instant.
   *
   * @returns The delay in milliseconds.
   */
  #backoff(): number {
    const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** (this.#attempt - 1));
    return ceiling * (0.5 + this.#deps.random() * 0.5);
  }

  /**
   * Wait and then open a new socket.
   *
   * @param delayMs - The wait in milliseconds.
   */
  #schedule(delayMs: number): void {
    this.#emit({ status: "reconnecting", reason: null });
    this.#timer = setTimeout(() => {
      this.#timer = null;
      this.#open();
    }, delayMs);
  }

  /**
   * Renew the session and reconnect.
   *
   * Stops after too many renewals in a row, stops with a message when the session cannot be
   * recovered, and backs off like any other fault when the renewal itself fails.
   */
  #renew(): void {
    this.#renewals += 1;
    if (this.#renewals > MAX_RENEWALS) {
      this.#emit({ status: "stopped", reason: LOST });
      return;
    }
    this.#emit({ status: "reconnecting", reason: null });
    const epoch = this.#epoch;
    this.#deps.renew().then(
      (renewed) => {
        if (epoch !== this.#epoch) {
          return;
        }
        if (renewed) {
          this.#schedule(0);
        } else {
          this.#emit({ status: "stopped", reason: ENDED });
        }
      },
      () => {
        if (epoch === this.#epoch) {
          this.#attempt += 1;
          this.#schedule(this.#backoff());
        }
      },
    );
  }
}
