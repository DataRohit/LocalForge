/**
 * Email form.
 *
 * Edits the one mutable profile field. The profile shown everywhere changes only after the backend
 * accepts the new address, so no optimistic update can show a value the backend refused.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { updateProfile, useSubmission } from "../../api";
import { useSessionActions, useSignedInSession } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";

const schema = z.object({ email: z.string().min(1, "Enter your email address.") });

type Values = z.infer<typeof schema>;

/**
 * Render the email form.
 *
 * Starts from the current address, shows backend validation on the field, and confirms a saved
 * change. A failed save leaves the typed value in place for correction.
 *
 * @returns The form element.
 */
export function EmailForm(): JSX.Element {
  const { profile } = useSignedInSession();
  const actions = useSessionActions();
  const [saved, setSaved] = useState(false);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: profile.email },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    setSaved(false);
    actions.establish(await updateProfile(values));
    setSaved(true);
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Card>
      <Form label="Change your email" onSubmit={onSubmit}>
        {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
        {saved ? (
          <Alert kind="success" title="Email updated">
            Your email address was saved.
          </Alert>
        ) : null}
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          error={errors.email?.message}
          {...register("email")}
        />
        <div>
          <Button type="submit" busy={isSubmitting}>
            Save email
          </Button>
        </div>
      </Form>
    </Card>
  );
}
