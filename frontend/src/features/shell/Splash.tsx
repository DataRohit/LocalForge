/**
 * Loading screen.
 *
 * Shown in place of a protected screen while the session is being read. It announces the state
 * politely and shows no content that could flash before a redirect.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { Spinner } from "../../ui/Spinner";

/**
 * Render the loading screen.
 *
 * It announces the state politely and shows nothing that could flash before a redirect.
 * Only protected routes show it; public screens render immediately.
 *
 * @returns The loading element.
 */
export function Splash(): JSX.Element {
  return (
    <div className="p-6">
      <Spinner label="Loading your session…" />
    </div>
  );
}
