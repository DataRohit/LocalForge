/**
 * Session-loss signal.
 *
 * Lets the API client announce that the session could not be recovered without importing the
 * session layer. The session subscribes once and clears its state when notified.
 *
 * @packageDocumentation
 */

type Listener = () => void;

const listeners = new Set<Listener>();

/**
 * Subscribe to session loss.
 *
 * Registers a listener that runs each time the client gives up on a session.
 * The session layer subscribes once at startup and clears its state in the listener.
 *
 * @param listener - Function to call on session loss.
 * @returns A function that removes the listener.
 */
export function subscribeUnauthorized(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Announce session loss.
 *
 * Calls every registered listener. The client calls it once when a refresh or the single retry
 * after a refresh is rejected with 401.
 */
export function notifyUnauthorized(): void {
  for (const listener of [...listeners]) {
    listener();
  }
}
