/**
 * Online status hook.
 *
 * Reports whether the browser believes it is connected and re-renders when that changes.
 * Failed requests report offline separately through the API error kinds.
 *
 * @packageDocumentation
 */

import { useSyncExternalStore } from "react";

/**
 * Subscribe to connectivity changes.
 *
 * @param notify - Callback to run on each change.
 * @returns A function that removes the listeners.
 */
function subscribe(notify: () => void): () => void {
  window.addEventListener("online", notify);
  window.addEventListener("offline", notify);
  return () => {
    window.removeEventListener("online", notify);
    window.removeEventListener("offline", notify);
  };
}

/**
 * Read whether the browser is online.
 *
 * @returns True when the browser reports a connection.
 */
function snapshot(): boolean {
  return navigator.onLine;
}

/**
 * Track browser connectivity.
 *
 * It reflects the browser's view only; a failed request is reported separately as offline.
 * Screens use it for the persistent offline notice, never to block input.
 *
 * @returns True while the browser reports a connection.
 */
export function useOnlineStatus(): boolean {
  return useSyncExternalStore(subscribe, snapshot);
}
