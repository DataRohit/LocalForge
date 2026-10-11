/**
 * Problem alert.
 *
 * Shows a failed request as an alert: a title chosen from the kind of failure, the message, any
 * messages that matched no field, the wait time for throttling, and a reference to quote.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { Alert } from "./Alert";

/**
 * The part of a problem the alert shows.
 *
 * Structurally the same as the API problem, declared here so this layer needs no API import.
 */
export interface ProblemView {
  readonly kind: string;
  readonly message: string;
  readonly retryAfterSeconds: number | null;
  readonly requestId: string | null;
}

/**
 * Problem alert props.
 *
 * The problem to show and any extra messages that matched no form field.
 */
export interface ProblemAlertProps {
  readonly problem: ProblemView;
  readonly details?: readonly string[];
}

const TITLES: Readonly<Record<string, string>> = {
  offline: "You are offline",
  throttled: "Too many attempts",
  validation: "Please correct the form",
};

/**
 * Render a problem as an alert.
 *
 * Offline and throttled problems use the warning tone because the person can resolve them by
 * waiting; everything else is an error. The request reference helps when reporting a failure.
 *
 * @param props - The alert props.
 * @returns The alert element.
 */
export function ProblemAlert({ problem, details = [] }: ProblemAlertProps): JSX.Element {
  const waiting = problem.kind === "offline" || problem.kind === "throttled";
  return (
    <Alert
      kind={waiting ? "warning" : "error"}
      title={TITLES[problem.kind] ?? "The request failed"}
    >
      <p>{problem.message}</p>
      {details.map((detail) => (
        <p key={detail}>{detail}</p>
      ))}
      {problem.retryAfterSeconds === null ? null : (
        <p>Try again in {problem.retryAfterSeconds} seconds.</p>
      )}
      {problem.requestId === null || problem.kind === "validation" ? null : (
        <p className="mt-1 text-xs">Reference: {problem.requestId}</p>
      )}
    </Alert>
  );
}
