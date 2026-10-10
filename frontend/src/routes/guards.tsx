/**
 * Route guards.
 *
 * Decide what a route renders from the session: the loading and error screens, a redirect to
 * sign-in, the no-access screen, or the protected content. Capabilities come from the backend.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";
import { Navigate } from "react-router";

import { NoAccess } from "../features/shell/NoAccess";
import { SessionError } from "../features/shell/SessionError";
import { Splash } from "../features/shell/Splash";
import { PATHS, useSession } from "../session";
import type { Capability } from "../session";

/**
 * Capability guard props.
 *
 * The capability the screen needs and the screen itself.
 */
export interface RequireCapabilityProps {
  readonly capability: Capability;
  readonly children: ReactNode;
}

/**
 * Public-only guard props.
 *
 * The screen that only makes sense without a session, such as sign-in.
 */
export interface PublicOnlyProps {
  readonly children: ReactNode;
}

/**
 * Render a screen only for an account that holds a capability.
 *
 * Waits while the session loads, shows the error screen with a retry when the session could not
 * be read, sends signed-out visitors to sign-in, and shows no-access when the capability is
 * missing.
 *
 * @param props - The guard props.
 * @returns The guarded content or the screen that replaces it.
 */
export function RequireCapability({ capability, children }: RequireCapabilityProps): JSX.Element {
  const session = useSession();
  if (session.status === "loading") {
    return <Splash />;
  }
  if (session.status === "error") {
    return <SessionError problem={session.problem} retry={session.retry} />;
  }
  if (session.status === "signedOut") {
    return <Navigate to={PATHS.signIn} replace />;
  }
  return session.can(capability) ? <>{children}</> : <NoAccess />;
}

/**
 * Render a screen only when there is no session.
 *
 * Sends a signed-in account to its account page. While the session loads or fails to load, the
 * screen still renders, because a signed-out visitor must be able to use it offline.
 *
 * @param props - The guard props.
 * @returns The screen or a redirect.
 */
export function PublicOnly({ children }: PublicOnlyProps): JSX.Element {
  const session = useSession();
  return session.status === "signedIn" ? <Navigate to={PATHS.account} replace /> : <>{children}</>;
}

/**
 * Redirect the start page.
 *
 * Signed-in accounts go to their account page and signed-out visitors go to sign-in.
 * A failed session read shows the retry screen instead of guessing a destination.
 *
 * @returns The redirect, or the loading or error screen while the session is unknown.
 */
export function HomeRedirect(): JSX.Element {
  const session = useSession();
  if (session.status === "loading") {
    return <Splash />;
  }
  if (session.status === "error") {
    return <SessionError problem={session.problem} retry={session.retry} />;
  }
  return <Navigate to={session.status === "signedIn" ? PATHS.account : PATHS.signIn} replace />;
}
