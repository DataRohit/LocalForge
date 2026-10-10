/**
 * Spinner primitive.
 *
 * A loading indicator with a visible label, announced politely through the status role.
 * The animation is decoration and is hidden from assistive technology.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

/**
 * Spinner props.
 *
 * The text shown beside the indicator.
 */
export interface SpinnerProps {
  readonly label: string;
}

/**
 * Render a loading indicator.
 *
 * Shows the label as text, so the loading state never depends on motion or colour.
 * The status role announces the label to assistive technology without interrupting.
 *
 * @param props - The spinner props.
 * @returns The indicator element.
 */
export function Spinner({ label }: SpinnerProps): JSX.Element {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-muted">
      <span
        aria-hidden="true"
        className="inline-block size-4 animate-spin rounded-full border-2 border-line border-t-brand"
      />
      {label}
    </p>
  );
}
