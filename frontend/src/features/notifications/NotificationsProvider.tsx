/**
 * Notifications provider.
 *
 * Owns the one notification connection. It connects only while a signed-in account may receive
 * notifications and the browser is online, and it stops the connection on sign-out, on going
 * offline, and on unmount. Notifications are kept per account and never shown to another.
 *
 * @packageDocumentation
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX, ReactNode } from "react";

import { renewSession } from "../../api";
import type { ConnectNotifications } from "../../api";
import { useSession } from "../../session";
import { useOnlineStatus } from "../../ui/useOnlineStatus";
import { NotificationConnection } from "./connection";
import type { ConnectionState, NotificationItem } from "./connection";
import { NotificationsContext } from "./context";
import type { FeedView, NotificationsContextValue } from "./context";

/**
 * Notifications provider props.
 *
 * The function that opens the socket, supplied by the caller so tests can replace it, and the
 * subtree that may read the feed.
 */
export interface NotificationsProviderProps {
  readonly connect: ConnectNotifications;
  readonly children: ReactNode;
}

interface Held {
  readonly account: string | null;
  readonly state: ConnectionState;
}

interface Kept {
  readonly account: string | null;
  readonly items: readonly NotificationItem[];
}

const INITIAL: ConnectionState = { status: "connecting", reason: null, items: [] };
const IDLE: FeedView = { status: "idle", reason: null, items: [] };

/**
 * Provide the notification feed.
 *
 * The state is tagged with the account it belongs to, so after a sign-out or an account change the
 * previous account's notifications are never read. Going offline pauses the connection and keeps
 * what was received; coming back online opens a new connection with the same notifications.
 *
 * @param props - The provider props.
 * @returns The provider element.
 */
export function NotificationsProvider({
  connect,
  children,
}: NotificationsProviderProps): JSX.Element {
  const session = useSession();
  const online = useOnlineStatus();
  const account =
    session.status === "signedIn" && session.can("notifications:receive")
      ? session.profile.id
      : null;
  const [held, setHeld] = useState<Held>({ account: null, state: INITIAL });
  const [restarts, setRestarts] = useState(0);
  const kept = useRef<Kept>({ account: null, items: [] });
  const active = useRef<NotificationConnection | null>(null);

  useEffect(() => {
    if (account === null || !online) {
      return undefined;
    }
    const seed = kept.current.account === account ? kept.current.items : [];
    const connection = new NotificationConnection(
      {
        connect,
        renew: renewSession,
        onChange: (next) => {
          kept.current = { account, items: next.items };
          setHeld({ account, state: next });
        },
        random: Math.random,
        now: Date.now,
      },
      seed,
    );
    active.current = connection;
    connection.start();
    return () => {
      connection.stop();
      active.current = null;
    };
  }, [account, connect, online, restarts]);

  const retry = useCallback(() => {
    setRestarts((count) => count + 1);
  }, []);
  const clear = useCallback(() => {
    kept.current = { account: kept.current.account, items: [] };
    active.current?.clear();
    setHeld((previous) => ({ ...previous, state: { ...previous.state, items: [] } }));
  }, []);

  const owned = held.account === account ? held.state : INITIAL;
  let view: FeedView;
  if (account === null) {
    view = IDLE;
  } else if (online) {
    view = owned;
  } else {
    view = { status: "offline", reason: null, items: owned.items };
  }
  const value = useMemo<NotificationsContextValue>(
    () => ({ view, retry, clear }),
    [view, retry, clear],
  );
  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}
