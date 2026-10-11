/**
 * Change-password page.
 *
 * Replaces the signed-in account's password after checking the current one. The backend revokes
 * every session when it succeeds, so the page ends the local session and returns to sign-in.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { changePassword, useSubmission } from "../../api";
import { PATHS, useSessionActions } from "../../session";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { TextLink } from "../../ui/TextLink";

const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password."),
    new_password: z.string().min(1, "Choose a new password."),
    new_password_confirm: z.string().min(1, "Repeat the new password."),
  })
  .refine((values) => values.new_password === values.new_password_confirm, {
    path: ["new_password_confirm"],
    message: "The passwords do not match.",
  });

type Values = z.infer<typeof schema>;

/**
 * Render the change-password page.
 *
 * On success the local session is dropped with a notice, because the backend has ended every
 * session of the account, and the route guard then shows the sign-in page.
 *
 * @returns The page element.
 */
export function ChangePasswordPage(): JSX.Element {
  const actions = useSessionActions();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", new_password_confirm: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    await changePassword(values);
    actions.drop("password-changed");
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Page title="Change your password">
      <Card>
        <Form label="Change your password" onSubmit={onSubmit}>
          {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            error={errors.current_password?.message}
            {...register("current_password")}
          />
          <TextField
            label="New password"
            type="password"
            autoComplete="new-password"
            hint="You will be signed out on every device after the change."
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
      <p className="text-sm">
        <TextLink to={PATHS.account}>Back to your account</TextLink>
      </p>
    </Page>
  );
}
