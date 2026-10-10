/**
 * Account page.
 *
 * Shows the signed-in account's public profile. Editing and deletion arrive with the profile
 * management ticket; this screen is the signed-in landing page.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { useSignedInSession } from "../../session";
import { Card } from "../../ui/Card";
import { Page } from "../../ui/Page";

/**
 * Render the account page.
 *
 * Lists the username and email from the session profile.
 * The page is read-only for now and sits behind the account:read capability guard.
 *
 * @returns The page element.
 */
export function AccountPage(): JSX.Element {
  const { profile } = useSignedInSession();
  return (
    <Page title="Your account">
      <Card>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          <dt className="text-muted">Username</dt>
          <dd className="font-medium break-all">{profile.username}</dd>
          <dt className="text-muted">Email</dt>
          <dd className="font-medium break-all">{profile.email}</dd>
        </dl>
      </Card>
    </Page>
  );
}
