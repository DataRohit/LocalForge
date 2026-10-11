/**
 * Form submission hook.
 *
 * Connects a React Hook Form instance to the error model: it runs the action, attaches backend
 * field errors to their inputs, and keeps the remaining failure for a form-level alert.
 *
 * @packageDocumentation
 */

import { useState } from "react";
import type { BaseSyntheticEvent } from "react";
import type { FieldValues, Path, UseFormReturn } from "react-hook-form";

import { toProblem } from "./problem";
import type { Problem } from "./problem";

/**
 * Result of the submission hook.
 *
 * The latest failure, the messages that matched no input, and the submit handler for the form.
 */
export interface Submission {
  readonly problem: Problem | null;
  readonly details: readonly string[];
  readonly onSubmit: (event?: BaseSyntheticEvent) => Promise<void>;
}

/**
 * Failure state of a submission.
 *
 * The problem to show and the field messages that no input could carry.
 */
interface Failure {
  readonly problem: Problem | null;
  readonly details: readonly string[];
}

const NO_FAILURE: Failure = { problem: null, details: [] };

/**
 * Submit a form through an action.
 *
 * Clears the previous failure, runs the action with the validated values, and on error maps every
 * backend field error to the input of that name, focusing the first. Messages for names the form
 * does not have, such as non-field errors, are returned as details.
 *
 * @param form - The form instance whose values are submitted.
 * @param action - Performs the request; throws the client's errors on failure.
 * @returns The failure state and the submit handler.
 */
export function useSubmission<Values extends FieldValues>(
  form: UseFormReturn<Values>,
  action: (values: Values) => Promise<void>,
): Submission {
  const [failure, setFailure] = useState<Failure>(NO_FAILURE);
  const { handleSubmit, setError, getValues } = form;
  const onSubmit = handleSubmit(async (values) => {
    setFailure(NO_FAILURE);
    try {
      await action(values);
    } catch (error) {
      const problem = toProblem(error);
      const known = new Set(Object.keys(getValues()));
      const details: string[] = [];
      let focused = false;
      for (const [field, messages] of Object.entries(problem.fields)) {
        if (known.has(field)) {
          setError(
            field as Path<Values>,
            { type: "server", message: messages.join(" ") },
            { shouldFocus: !focused },
          );
          focused = true;
        } else {
          details.push(...messages);
        }
      }
      setFailure({ problem, details });
    }
  });
  return { problem: failure.problem, details: failure.details, onSubmit };
}
