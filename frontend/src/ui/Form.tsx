/**
 * Form primitive.
 *
 * A labelled form element that leaves validation to the form library and the backend, so the
 * browser's own bubbles never compete with the accessible error messages.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode, SubmitEvent } from "react";

/**
 * Form props.
 *
 * The accessible name, the submit handler, and the fields and actions.
 */
export interface FormProps {
  readonly label: string;
  readonly onSubmit: (event: SubmitEvent<HTMLFormElement>) => Promise<void> | void;
  readonly children: ReactNode;
}

/**
 * Render a form.
 *
 * Disables native validation and ignores the returned promise, because the handler reports its
 * own failures through the interface.
 *
 * @param props - The form props.
 * @returns The form element.
 */
export function Form({ label, onSubmit, children }: FormProps): JSX.Element {
  return (
    <form
      aria-label={label}
      noValidate
      onSubmit={(event) => {
        void onSubmit(event);
      }}
      className="flex flex-col gap-4"
    >
      {children}
    </form>
  );
}
