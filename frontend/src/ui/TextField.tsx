/**
 * Text field primitive.
 *
 * A labelled input with an optional hint and error, wired so the label, hint, and error are
 * announced with the control. It accepts the handlers React Hook Form registers.
 *
 * @packageDocumentation
 */

import { useId } from "react";
import type { ChangeEventHandler, FocusEventHandler, JSX, Ref } from "react";

import { cn } from "./cn";

/**
 * Text field props.
 *
 * The visible label, the form name, the input type, optional hint and error text, and the
 * handlers and ref a form library supplies.
 */
export interface TextFieldProps {
  readonly label: string;
  readonly name: string;
  readonly type?: "text" | "email" | "password";
  readonly autoComplete?: string;
  readonly hint?: string;
  readonly error?: string;
  readonly onChange?: ChangeEventHandler<HTMLInputElement>;
  readonly onBlur?: FocusEventHandler<HTMLInputElement>;
  readonly ref?: Ref<HTMLInputElement>;
}

/**
 * Render a labelled text input.
 *
 * Links the hint and error to the input through aria-describedby and marks the input invalid when
 * an error is present. The error text carries the meaning, so colour is never the only signal.
 *
 * @param props - The field props.
 * @returns The field element.
 */
export function TextField({
  label,
  name,
  type = "text",
  autoComplete,
  hint,
  error,
  onChange,
  onBlur,
  ref,
}: TextFieldProps): JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [hint === undefined ? "" : hintId, error === undefined ? "" : errorId]
    .filter((value) => value !== "")
    .join(" ");
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        autoComplete={autoComplete}
        onChange={onChange}
        onBlur={onBlur}
        ref={ref}
        aria-invalid={error === undefined ? undefined : true}
        aria-describedby={described === "" ? undefined : described}
        className={cn(
          "min-h-11 rounded-card border bg-surface px-3 py-2 text-base",
          error === undefined ? "border-line" : "border-danger",
        )}
      />
      {hint === undefined ? null : (
        <p id={hintId} className="text-sm text-muted">
          {hint}
        </p>
      )}
      {error === undefined ? null : (
        <p id={errorId} className="text-sm font-medium text-danger">
          <span className="sr-only">Error:</span> {error}
        </p>
      )}
    </div>
  );
}
