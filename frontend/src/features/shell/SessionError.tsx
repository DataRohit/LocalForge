/**
 * Session error screen.
 *
 * Shown when the signed-in profile could not be read for a reason other than having no session,
 * such as being offline or the service being unavailable. It offers a retry.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import type { Problem } from "../../api";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Page } from "../../ui/Page";

/**
 * Session error props.
 *
 * The problem to describe and the function that tries again.
 */
export interface SessionErrorProps {
  readonly problem: Problem;
  readonly retry: () => void;
}

/**
 * Render the session error screen.
 *
 * Uses the warning tone for being offline, which the person can fix, and the error tone otherwise.
 * The retry button asks the session provider to read the profile again.
 *
 * @param props - The screen props.
 * @returns The screen element.
 */
export function SessionError({ problem, retry }: SessionErrorProps): JSX.Element {
  return (
    <Page title="Cannot load your session">
      <Alert
        kind={problem.kind === "offline" ? "warning" : "error"}
        title="Your session could not be checked."
      >
        {problem.message}
      </Alert>
      <div>
        <Button onClick={retry}>Try again</Button>
      </div>
    </Page>
  );
}
