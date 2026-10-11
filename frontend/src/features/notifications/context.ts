/**
 * Notifications context.
 *
 * Defines what the notification feed reads from the provider: the connection view, a way to start
 * again after a stop, and a way to clear the list.
 *
 * @packageDocumentation
 */

import { createContext } from "react";

import type { ConnectionStatus, NotificationItem } from "./connection";

/**
 * Feed status.
 *
 * The connection statuses, plus idle when the account does not receive notifications and offline
 * while the browser has no connection.
 */
export type FeedStatus = ConnectionStatus | "idle" | "offline";

/**
 * What the feed shows.
 *
 * The status, the reason when stopped, and the notifications received, newest first.
 */
export interface FeedView {
  readonly status: FeedStatus;
  readonly reason: string | null;
  readonly items: readonly NotificationItem[];
}

/**
 * Value of the notifications context.
 *
 * The view and the two actions a person can take on the feed.
 */
export interface NotificationsContextValue {
  readonly view: FeedView;
  readonly retry: () => void;
  readonly clear: () => void;
}

/**
 * Notifications context.
 *
 * Provided by the notifications provider and read with the notifications hook.
 */
export const NotificationsContext = createContext<NotificationsContextValue | null>(null);
