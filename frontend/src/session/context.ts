/**
 * Session types and context.
 *
 * Defines the session states every screen branches on and the actions that change them. The
 * session is derived from the profile endpoint and is never stored in browser storage.
 *
 * @packageDocumentation
 */

import { createContext } from "react";

import type { Problem, UserProfile } from "../api";

/**
 * A capability the backend grants.
 *
 * Capabilities guard client navigation only; the backend still authorizes every request.
 */
export type Capability = UserProfile["capabilities"][number];

/**
 * Session state.
 *
 * Loading while the profile is requested, signed out without a session, signed in with the
 * profile and a capability check, or an error when the profile could not be read.
 */
export type Session =
  | { readonly status: "loading" }
  | { readonly status: "signedOut" }
  | { readonly status: "error"; readonly problem: Problem; readonly retry: () => void }
  | {
      readonly status: "signedIn";
      readonly profile: UserProfile;
      readonly can: (capability: Capability) => boolean;
    };

/**
 * Reason the visitor was returned to sign-in.
 *
 * Held in memory for the sign-in page to explain, because a route guard redirects before the
 * screen that caused the sign-out could show anything.
 */
export type SignInNotice = "password-changed" | "session-ended";

/**
 * Session actions.
 *
 * Establish a session after sign-in, end it with a sign-out request, drop it locally when the
 * server has already ended it, or refresh the profile after the server changed it.
 */
export interface SessionActions {
  readonly establish: (profile: UserProfile) => void;
  readonly end: () => Promise<Problem | null>;
  readonly drop: (notice?: SignInNotice) => void;
  readonly refresh: () => Promise<void>;
}

/**
 * Value of the session context.
 *
 * The current session, the actions that change it, and the pending sign-in notice.
 */
export interface SessionContextValue {
  readonly session: Session;
  readonly actions: SessionActions;
  readonly notice: SignInNotice | null;
}

/**
 * Session context.
 *
 * Provided by the session provider and read through the session hooks.
 */
export const SessionContext = createContext<SessionContextValue | null>(null);
