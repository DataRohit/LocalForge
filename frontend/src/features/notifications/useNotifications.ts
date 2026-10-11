/**
 * Notifications hook.
 *
 * The way a screen reads the notification feed. It requires the notifications provider above it.
 * Reading outside the provider is a programming error and throws immediately.
 *
 * @packageDocumentation
 */

import { useContext } from "react";

import { NotificationsContext } from "./context";
import type { NotificationsContextValue } from "./context";

/**
 * Read the notification feed.
 *
 * Returns the connection view and the retry and clear actions.
 * The view changes as the connection opens, loses its socket, or stops.
 *
 * @returns The feed value.
 * @throws Error If called outside the notifications provider.
 */
export function useNotifications(): NotificationsContextValue {
  const value = useContext(NotificationsContext);
  if (value === null) {
    throw new Error("useNotifications needs a NotificationsProvider.");
  }
  return value;
}
