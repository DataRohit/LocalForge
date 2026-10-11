/**
 * Reset-username form.
 *
 * Collects the new username and confirms the reset with the credentials from the emailed link.
 * The credentials go in the request body only and are never shown or logged.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { confirmUsernameReset, toProblem, useSubmission } from "../../api";
import type { FragmentCredentials } from "../../session";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { tokenFailureMessage } from "./tokenFailure";

const schema = z.object({ new_username: z.string().min(1, "Choose a new username.") });

type Values = z.infer<typeof schema>;

/**
 * Reset-username form props.
 *
 * The link credentials and the two outcomes the page reacts to.
 */
export interface ResetUsernameFormProps {
  readonly credentials: FragmentCredentials;
  readonly onDone: () => void;
  readonly onTokenFailure: (message: string) => void;
}

/**
 * Render the reset-username form.
 *
 * A token failure is reported to the page, which forgets the credentials; any other failure stays
 * in the form so the username can be corrected.
 *
 * @param props - The form props.
 * @returns The form element.
 */
export function ResetUsernameForm({
  credentials,
  onDone,
  onTokenFailure,
}: ResetUsernameFormProps): JSX.Element {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { new_username: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    try {
      await confirmUsernameReset({ ...credentials, ...values });
    } catch (error) {
      const message = tokenFailureMessage("username", toProblem(error).code);
      if (message === null) {
        throw error;
      }
      onTokenFailure(message);
      return;
    }
    onDone();
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Card>
      <Form label="Choose a new username" onSubmit={onSubmit}>
        {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
        <TextField
          label="New username"
          autoComplete="username"
          error={errors.new_username?.message}
          {...register("new_username")}
        />
        <Button type="submit" busy={isSubmitting}>
          Change username
        </Button>
      </Form>
    </Card>
  );
}
