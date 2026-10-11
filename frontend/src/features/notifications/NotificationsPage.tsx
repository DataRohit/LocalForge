/**
 * Notifications page.
 *
 * Shows the live feed: the state of the connection, the notifications received while connected,
 * an explicit empty state, and a polite live region that announces each new notification.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Page } from "../../ui/Page";
import { ConnectionNotice } from "./ConnectionNotice";
import { describeEvent } from "./frame";
import { useNotifications } from "./useNotifications";

/**
 * Render the notifications page.
 *
 * There is no history: only what arrived while this page's account was connected is listed, and
 * the empty state says so. The live region is separate from the list so the whole list is not
 * read out again on every change.
 *
 * @returns The page element.
 */
export function NotificationsPage(): JSX.Element {
  const { view, retry, clear } = useNotifications();
  const latest = view.items[0];
  return (
    <Page title="Notifications">
      <ConnectionNotice view={view} onRetry={retry} />
      <div aria-live="polite" className="sr-only">
        {latest === undefined ? null : <span key={latest.id}>{describeEvent(latest.event)}</span>}
      </div>
      <Card>
        {view.items.length === 0 ? (
          <p>
            No notifications yet. Notifications that arrive while you are signed in appear here;
            earlier ones are not stored.
          </p>
        ) : (
          <>
            <ul className="flex flex-col divide-y divide-line">
              {view.items.map((item) => (
                <li key={item.id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span>{describeEvent(item.event)}</span>
                  <time
                    dateTime={new Date(item.receivedAt).toISOString()}
                    className="text-sm text-muted"
                  >
                    {new Date(item.receivedAt).toLocaleTimeString()}
                  </time>
                </li>
              ))}
            </ul>
            <div>
              <Button variant="secondary" onClick={clear}>
                Clear list
              </Button>
            </div>
          </>
        )}
      </Card>
    </Page>
  );
}
