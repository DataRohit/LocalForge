/**
 * Reset-username page.
 *
 * Finishes a username reset with the credentials the emailed link carried in the URL fragment. The
 * credentials were removed from the address bar at startup and live only in memory until used.
 *
 * @packageDocumentation
 */

import { useState } from "react";
import type { JSX } from "react";

import { PATHS, useFragmentCredentials } from "../../session";
import { Alert } from "../../ui/Alert";
import { Page } from "../../ui/Page";
import { TextLink } from "../../ui/TextLink";
import { ResetUsernameForm } from "./ResetUsernameForm";

type Outcome =
  | { readonly kind: "pending" }
  | { readonly kind: "done" }
  | { readonly kind: "dead"; readonly message: string };

/**
 * Render the reset-username page.
 *
 * Shows the form while the credentials are usable, a success message afterwards, and a link to
 * request a new email once the token cannot be reused or was never present.
 *
 * @returns The page element.
 */
export function ResetUsernamePage(): JSX.Element {
  const { credentials, consume } = useFragmentCredentials();
  const [outcome, setOutcome] = useState<Outcome>({ kind: "pending" });

  if (outcome.kind === "done") {
    return (
      <Page title="Username changed">
        <Alert kind="success" title="Your username was changed.">
          Sign in with your new username and your existing password.
        </Alert>
        <p>
          <TextLink to={PATHS.signIn}>Go to sign in</TextLink>
        </p>
      </Page>
    );
  }
  if (credentials === null) {
    return (
      <Page title="Choose a new username">
        <Alert
          kind="warning"
          title={outcome.kind === "dead" ? outcome.message : "This reset link cannot be used."}
        >
          Open the link from your email again, or ask for a new one.
        </Alert>
        <p>
          <TextLink to={PATHS.forgotUsername}>Request a new reset link</TextLink>
        </p>
      </Page>
    );
  }
  return (
    <Page title="Choose a new username">
      <ResetUsernameForm
        credentials={credentials}
        onDone={() => {
          consume();
          setOutcome({ kind: "done" });
        }}
        onTokenFailure={(message) => {
          consume();
          setOutcome({ kind: "dead", message });
        }}
      />
    </Page>
  );
}
