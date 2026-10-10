/**
 * Session hooks.
 *
 * The two ways a screen reads the session: the current state to branch on, and the actions that
 * change it. Both require the session provider above them in the tree.
 *
 * @packageDocumentation
 */

import { useContext } from "react";

import { SessionContext } from "./context";
import type { Session, SessionActions, SessionContextValue } from "./context";

/**
 * A session that is known to be signed in.
 *
 * The signed-in member of the session union, for screens a guard has already protected.
 */
export type SignedInSession = Extract<Session, { readonly status: "signedIn" }>;

/**
 * Read the session context.
 *
 * @returns The session and its actions.
 * @throws Error If called outside the session provider.
 */
function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("The session hooks need a SessionProvider.");
  }
  return value;
}

/**
 * Read the current session.
 *
 * The screen branches on the status instead of testing for missing data.
 * Guards and the shell use it to decide what to render.
 *
 * @returns The loading, signed-out, error, or signed-in state.
 */
export function useSession(): Session {
  return useSessionContext().session;
}

/**
 * Read the session actions.
 *
 * The actions are stable for a given session and safe to call from event handlers.
 * Ending a session reports a problem instead of throwing.
 *
 * @returns Functions to establish, end, or drop the session.
 */
export function useSessionActions(): SessionActions {
  return useSessionContext().actions;
}

/**
 * Read the session of a protected screen.
 *
 * Screens behind a route guard use this to read the profile without handling states the guard
 * already excluded.
 *
 * @returns The signed-in session.
 * @throws Error If the session is not signed in, which means a guard is missing.
 */
export function useSignedInSession(): SignedInSession {
  const session = useSession();
  if (session.status !== "signedIn") {
    throw new Error("This screen needs a signed-in session; protect its route with a guard.");
  }
  return session;
}
