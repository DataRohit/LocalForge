/**
 * Connection notice.
 *
 * Tells the person whether the live connection is working: connecting, live, reconnecting, paused
 * because the browser is offline, or stopped with the reason and a way to try again.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import type { FeedView } from "./context";

/**
 * Connection notice props.
 *
 * The feed view to describe and the function that starts a new connection.
 */
export interface ConnectionNoticeProps {
  readonly view: FeedView;
  readonly onRetry: () => void;
}

/**
 * Render the connection notice.
 *
 * Every status has words as well as a tone, so the state never depends on colour. The stopped
 * state offers a button because the person can often fix it, for example by coming back later.
 *
 * @param props - The notice props.
 * @returns The notice element, or nothing when the feed is idle.
 */
export function ConnectionNotice({ view, onRetry }: ConnectionNoticeProps): JSX.Element | null {
  switch (view.status) {
    case "connecting":
      return <Alert kind="info" title="Connecting…" />;
    case "open":
      return (
        <Alert kind="success" title="Live">
          New notifications appear here as they happen.
        </Alert>
      );
    case "reconnecting":
      return (
        <Alert kind="warning" title="Reconnecting…">
          The connection was lost. Trying again.
        </Alert>
      );
    case "offline":
      return (
        <Alert kind="warning" title="Notifications paused">
          They resume when the connection returns. What you already received stays below.
        </Alert>
      );
    case "stopped":
      return (
        <Alert kind="error" title="Notifications stopped">
          <p>{view.reason}</p>
          <div className="mt-3">
            <Button variant="secondary" onClick={onRetry}>
              Try again
            </Button>
          </div>
        </Alert>
      );
    case "idle":
      return null;
  }
}
