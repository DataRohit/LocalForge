/**
 * Button primitive.
 *
 * A semantic button with three variants, a busy state that blocks double submission, and a
 * visible focus style. It never receives arbitrary DOM props.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";

import { cn } from "./cn";

/**
 * Button props.
 *
 * The label content, the button type and variant, the busy and disabled flags, and the click
 * handler.
 */
export interface ButtonProps {
  readonly children: ReactNode;
  readonly type?: "button" | "submit";
  readonly variant?: "primary" | "secondary" | "danger";
  readonly busy?: boolean;
  readonly disabled?: boolean;
  readonly onClick?: () => void;
}

const VARIANTS = {
  primary: "bg-brand text-brand-ink hover:bg-brand-strong",
  secondary: "border border-line bg-surface text-ink hover:bg-canvas",
  danger: "bg-danger text-brand-ink hover:opacity-90",
} as const;

/**
 * Render a button.
 *
 * Disables itself while busy so a form cannot be submitted twice, and reports the busy state to
 * assistive technology.
 *
 * @param props - The button props.
 * @returns The button element.
 */
export function Button({
  children,
  type = "button",
  variant = "primary",
  busy = false,
  disabled = false,
  onClick,
}: ButtonProps): JSX.Element {
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-11 items-center justify-center rounded-card px-4 py-2 text-sm font-semibold",
        "disabled:cursor-not-allowed disabled:opacity-60",
        VARIANTS[variant],
      )}
    >
      {children}
    </button>
  );
}
