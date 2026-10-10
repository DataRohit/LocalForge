/**
 * No-access screen.
 *
 * Shown when the signed-in account lacks the capability a screen needs. The backend decides
 * capabilities; this screen only explains the result.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { Alert } from "../../ui/Alert";
import { Page } from "../../ui/Page";

/**
 * Render the no-access screen.
 *
 * The route guard renders it when the account lacks a capability, so the screen
 * explains the result without implying the page exists for someone else.
 *
 * @returns The screen element.
 */
export function NoAccess(): JSX.Element {
  return (
    <Page title="Not available">
      <Alert kind="info">Your account does not have access to this page.</Alert>
    </Page>
  );
}
