/**
 * Account activation page.
 *
 * Activates an account with the credentials the emailed link carried in the URL fragment. The
 * credentials were removed from the address bar at startup and are sent in the request body only.
 *
 * @packageDocumentation
 */

import { useState } from "react";
import type { JSX } from "react";

import { activate, toProblem } from "../../api";
import type { Problem } from "../../api";
import { PATHS, useFragmentCredentials } from "../../session";
import type { FragmentCredentials } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextLink } from "../../ui/TextLink";

const TOKEN_MESSAGES: Readonly<Record<string, string>> = {
  activation_token_expired: "This activation link has expired. Request a new one.",
  activation_token_used: "This activation link was already used. Try signing in.",
  activation_token_foreign: "This activation link is not valid. Request a new one.",
  activation_token_malformed: "This activation link is not valid. Request a new one.",
};

type Status = "ready" | "busy" | "done";

/**
 * Render the activation page.
 *
 * Needs an explicit click, so a mail scanner or link preview that loads the page cannot consume
 * the single-use token. A token failure forgets the credentials because they cannot be reused.
 *
 * @returns The page element.
 */
export function ActivatePage(): JSX.Element {
  const { credentials, consume } = useFragmentCredentials();
  const [status, setStatus] = useState<Status>("ready");
  const [problem, setProblem] = useState<Problem | null>(null);

  const submit = async (current: FragmentCredentials): Promise<void> => {
    setStatus("busy");
    setProblem(null);
    try {
      await activate(current);
      consume();
      setStatus("done");
    } catch (error) {
      const failure = toProblem(error);
      if (TOKEN_MESSAGES[failure.code ?? ""] !== undefined) {
        consume();
      }
      setProblem(failure);
      setStatus("ready");
    }
  };

  const tokenMessage = TOKEN_MESSAGES[problem?.code ?? ""];

  if (status === "done") {
    return (
      <Page title="Account activated">
        <Alert kind="success" title="Your account is active.">
          You can now sign in.
        </Alert>
        <p>
          <TextLink to={PATHS.signIn}>Go to sign in</TextLink>
        </p>
      </Page>
    );
  }
  if (credentials === null) {
    return (
      <Page title="Activate your account">
        <Alert kind="warning" title={tokenMessage ?? "This activation link cannot be used."}>
          Open the link from your activation email again, or ask for a new one.
        </Alert>
        <p>
          <TextLink to={PATHS.resendActivation}>Resend the activation email</TextLink>
        </p>
      </Page>
    );
  }
  return (
    <Page title="Activate your account">
      <Card>
        <p>Confirm that you want to activate the account this link was sent for.</p>
        {problem === null ? null : <ProblemAlert problem={problem} />}
        <div>
          <Button
            busy={status === "busy"}
            onClick={() => {
              void submit(credentials);
            }}
          >
            Activate account
          </Button>
        </div>
      </Card>
    </Page>
  );
}
