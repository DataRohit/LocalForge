/**
 * Application root component.
 *
 * Renders the landing content until the shell and routes arrive in later tickets.
 * It exists so the toolchain builds, tests, and measures a real component from the first commit.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

/**
 * Render the application root.
 *
 * Returns the single main landmark with the product name as the page heading.
 * Later tickets replace this body with the router and session providers.
 *
 * @returns The root element tree.
 */
export function App(): JSX.Element {
  return (
    <main className="mx-auto max-w-2xl p-6">
      <h1 className="text-2xl font-semibold">LocalForge</h1>
    </main>
  );
}
