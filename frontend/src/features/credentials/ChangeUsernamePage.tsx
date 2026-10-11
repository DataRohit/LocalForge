/**
 * Change-username page.
 *
 * Replaces the signed-in account's username after checking the current password. The session
 * stays valid, so the page refreshes the profile and the new name shows everywhere at once.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { changeUsername, useSubmission } from "../../api";
import { PATHS, useSessionActions, useSignedInSession } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { TextLink } from "../../ui/TextLink";

const schema = z.object({
  current_password: z.string().min(1, "Enter your current password."),
  new_username: z.string().min(1, "Choose a new username."),
});

type Values = z.infer<typeof schema>;

/**
 * Render the change-username page.
 *
 * Shows the current username, and after a successful change clears the form and confirms the new
 * name. The current password is never kept once the request has been sent.
 *
 * @returns The page element.
 */
export function ChangeUsernamePage(): JSX.Element {
  const { profile } = useSignedInSession();
  const actions = useSessionActions();
  const [changedTo, setChangedTo] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_username: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    setChangedTo(null);
    await changeUsername(values);
    await actions.refresh();
    form.reset();
    setChangedTo(values.new_username);
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Page title="Change your username">
      {changedTo === null ? null : (
        <Alert kind="success" title="Username changed">
          Your username is now {changedTo}.
        </Alert>
      )}
      <Card>
        <Form label="Change your username" onSubmit={onSubmit}>
          {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
          <p className="text-sm text-muted">
            Current username: <strong className="text-ink">{profile.username}</strong>
          </p>
          <TextField
            label="New username"
            autoComplete="username"
            error={errors.new_username?.message}
            {...register("new_username")}
          />
          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            error={errors.current_password?.message}
            {...register("current_password")}
          />
          <Button type="submit" busy={isSubmitting}>
            Change username
          </Button>
        </Form>
      </Card>
      <p className="text-sm">
        <TextLink to={PATHS.account}>Back to your account</TextLink>
      </p>
    </Page>
  );
}
