/**
 * Alert primitive.
 *
 * A message box for errors, warnings, successes, and notes. Errors use the alert role so they are
 * announced at once; the other kinds use the status role and wait for a pause.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";

import { cn } from "./cn";

/**
 * Alert props.
 *
 * The kind that sets the tone and the role, an optional bold title, and the message content.
 */
export interface AlertProps {
  readonly kind: "error" | "warning" | "success" | "info";
  readonly title?: string;
  readonly children?: ReactNode;
}

const TONES = {
  error: "border-danger bg-danger-soft text-danger",
  warning: "border-warning bg-warning-soft text-warning",
  success: "border-success bg-success-soft text-success",
  info: "border-line bg-surface text-ink",
} as const;

/**
 * Render an alert.
 *
 * The title and message carry the meaning in text, so the colour is decoration, not the signal.
 * Errors use the alert role so assistive technology announces them at once.
 *
 * @param props - The alert props.
 * @returns The alert element.
 */
export function Alert({ kind, title, children }: AlertProps): JSX.Element {
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      className={cn("rounded-card border-l-4 p-4 text-sm", TONES[kind])}
    >
      {title === undefined ? null : <p className="font-semibold">{title}</p>}
      {children === undefined ? null : (
        <div className={cn(title !== undefined && "mt-1")}>{children}</div>
      )}
    </div>
  );
}
