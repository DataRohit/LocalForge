/**
 * Delete-account form.
 *
 * The confirmation shown inside the dialog: a plain statement of what deletion does, the current
 * password, and an explicit delete button beside a cancel button.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { deleteAccount, useSubmission } from "../../api";
import { useSessionActions } from "../../session";
import { Button } from "../../ui/Button";
import { Form } from "../../ui/Form";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";

const schema = z.object({ current_password: z.string().min(1, "Enter your current password.") });

type Values = z.infer<typeof schema>;

/**
 * Delete-account form props.
 *
 * The handler that closes the dialog without deleting anything.
 */
export interface DeleteAccountFormProps {
  readonly onCancel: () => void;
}

/**
 * Render the delete-account form.
 *
 * On success the local session is dropped with a notice and every cached query is cleared, so the
 * route guard shows sign-in and nothing of the account remains on screen.
 *
 * @param props - The form props.
 * @returns The form element.
 */
export function DeleteAccountForm({ onCancel }: DeleteAccountFormProps): JSX.Element {
  const actions = useSessionActions();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    await deleteAccount(values);
    actions.drop("account-deleted");
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Form label="Delete your account" onSubmit={onSubmit}>
      <p>
        This permanently deletes your account and cannot be undone. Enter your current password to
        confirm.
      </p>
      {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
      <TextField
        label="Current password"
        type="password"
        autoComplete="current-password"
        error={errors.current_password?.message}
        {...register("current_password")}
      />
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="danger" busy={isSubmitting}>
          Delete my account
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Form>
  );
}
