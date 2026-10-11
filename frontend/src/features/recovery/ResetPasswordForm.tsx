/**
 * Reset-password form.
 *
 * Collects the new password twice and confirms the reset with the credentials from the emailed
 * link. The credentials go in the request body only and are never shown or logged.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { confirmPasswordReset, toProblem, useSubmission } from "../../api";
import type { FragmentCredentials } from "../../session";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { tokenFailureMessage } from "./tokenFailure";

const schema = z
  .object({
    new_password: z.string().min(1, "Choose a new password."),
    new_password_confirm: z.string().min(1, "Repeat the new password."),
  })
  .refine((values) => values.new_password === values.new_password_confirm, {
    path: ["new_password_confirm"],
    message: "The passwords do not match.",
  });

type Values = z.infer<typeof schema>;

/**
 * Reset-password form props.
 *
 * The link credentials and the two outcomes the page reacts to.
 */
export interface ResetPasswordFormProps {
  readonly credentials: FragmentCredentials;
  readonly onDone: () => void;
  readonly onTokenFailure: (message: string) => void;
}

/**
 * Render the reset-password form.
 *
 * A token failure is reported to the page, which forgets the credentials; any other failure stays
 * in the form so the password can be corrected.
 *
 * @param props - The form props.
 * @returns The form element.
 */
export function ResetPasswordForm({
  credentials,
  onDone,
  onTokenFailure,
}: ResetPasswordFormProps): JSX.Element {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { new_password: "", new_password_confirm: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    try {
      await confirmPasswordReset({ ...credentials, ...values });
    } catch (error) {
      const message = tokenFailureMessage("password", toProblem(error).code);
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
      <Form label="Choose a new password" onSubmit={onSubmit}>
        {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          error={errors.new_password?.message}
          {...register("new_password")}
        />
        <TextField
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          error={errors.new_password_confirm?.message}
          {...register("new_password_confirm")}
        />
        <Button type="submit" busy={isSubmitting}>
          Change password
        </Button>
      </Form>
    </Card>
  );
}
