/**
 * Application shell.
 *
 * The layout around every screen: a skip link, the header with capability-driven navigation and
 * sign-out, an offline notice, the focusable main region, and the footer.
 *
 * @packageDocumentation
 */

import { useEffect, useRef, useState } from "react";
import type { JSX, MouseEvent } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import type { Problem } from "../../api";
import { PATHS, useSession, useSessionActions } from "../../session";
import type { Capability } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { useOnlineStatus } from "../../ui/useOnlineStatus";

interface NavigationItem {
  readonly to: string;
  readonly label: string;
  readonly capability: Capability;
}

const NAVIGATION: readonly NavigationItem[] = [
  { to: PATHS.account, label: "Account", capability: "account:read" },
  { to: PATHS.accountPassword, label: "Password", capability: "credentials:change" },
  { to: PATHS.accountUsername, label: "Username", capability: "credentials:change" },
  { to: PATHS.notifications, label: "Notifications", capability: "notifications:receive" },
];

const LINK_CLASS = "text-sm font-medium underline-offset-4 hover:underline";

/**
 * Render the application shell.
 *
 * Moves focus to the main region after every route change so keyboard and screen reader users
 * land on the new content. Navigation lists only what the backend granted.
 *
 * @returns The layout element with the routed screen in its outlet.
 */
export function Shell(): JSX.Element {
  const session = useSession();
  const actions = useSessionActions();
  const online = useOnlineStatus();
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const lastPath = useRef(location.pathname);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutProblem, setSignOutProblem] = useState<Problem | null>(null);

  useEffect(() => {
    if (lastPath.current !== location.pathname) {
      lastPath.current = location.pathname;
      main.current?.focus();
    }
  }, [location.pathname]);

  const skip = (event: MouseEvent<HTMLAnchorElement>): void => {
    event.preventDefault();
    main.current?.focus();
  };

  const signOut = (): void => {
    setSigningOut(true);
    void actions.end().then((problem) => {
      setSignOutProblem(problem);
      setSigningOut(false);
    });
  };

  const items =
    session.status === "signedIn" ? NAVIGATION.filter((item) => session.can(item.capability)) : [];

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        onClick={skip}
        className="sr-only bg-surface p-3 text-ink focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to main content
      </a>
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-6 gap-y-2 p-4">
          <Link to={PATHS.home} className="text-lg font-semibold">
            LocalForge
          </Link>
          <nav aria-label="Primary" className="flex-1">
            <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
              {session.status === "signedOut" ? (
                <>
                  <li>
                    <NavLink to={PATHS.signIn} className={LINK_CLASS}>
                      Sign in
                    </NavLink>
                  </li>
                  <li>
                    <NavLink to={PATHS.register} className={LINK_CLASS}>
                      Register
                    </NavLink>
                  </li>
                </>
              ) : null}
              {items.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to} end className={LINK_CLASS}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          {session.status === "signedIn" ? (
            <Button variant="secondary" busy={signingOut} onClick={signOut}>
              Sign out
            </Button>
          ) : null}
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-2 px-4 empty:hidden">
        {online ? null : (
          <Alert kind="warning" title="You are offline">
            Changes cannot be saved until the connection returns. Anything you typed is kept.
          </Alert>
        )}
        {signOutProblem === null ? null : (
          <Alert kind="error" title="Sign-out did not complete">
            {signOutProblem.message}
          </Alert>
        )}
        {session.status === "signedIn" && items.length === 0 ? (
          <Alert kind="info">No features are available for this account.</Alert>
        ) : null}
      </div>
      <main id="main" ref={main} tabIndex={-1} className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-line p-4 text-center text-sm text-muted">
        LocalForge
      </footer>
    </div>
  );
}
