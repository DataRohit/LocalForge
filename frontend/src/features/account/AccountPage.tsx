/**
 * Account page.
 *
 * Shows the signed-in account's public profile and, for the capabilities the backend granted,
 * the forms to change the email and to delete the account.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { useSignedInSession } from "../../session";
import { Alert } from "../../ui/Alert";
import { Card } from "../../ui/Card";
import { Page } from "../../ui/Page";
import { DeleteAccountSection } from "./DeleteAccountSection";
import { EmailForm } from "./EmailForm";

/**
 * Render the account page.
 *
 * Lists the username and email, then offers editing and deletion only when the matching
 * capability is granted. An account with neither sees a short explanation instead.
 *
 * @returns The page element.
 */
export function AccountPage(): JSX.Element {
  const { profile, can } = useSignedInSession();
  const canUpdate = can("account:update");
  const canDelete = can("account:delete");
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
      {canUpdate ? <EmailForm /> : null}
      {canDelete ? <DeleteAccountSection /> : null}
      {canUpdate || canDelete ? null : (
        <Alert kind="info">Editing and deleting are not available for this account.</Alert>
      )}
    </Page>
  );
}
