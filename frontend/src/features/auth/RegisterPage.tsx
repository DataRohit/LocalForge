/**
 * Registration page.
 *
 * Collects the account details and registers through the user collection route. The accepted
 * message is identical whether or not the address was already in use, so it reveals nothing.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { register as registerAccount, useSubmission } from "../../api";
import { PATHS } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { TextLink } from "../../ui/TextLink";

const schema = z
  .object({
    username: z.string().min(1, "Choose a username."),
    email: z.string().min(1, "Enter your email address."),
    password: z.string().min(1, "Choose a password."),
    password_confirm: z.string().min(1, "Repeat the password."),
  })
  .refine((values) => values.password === values.password_confirm, {
    path: ["password_confirm"],
    message: "The passwords do not match.",
  });

type Values = z.infer<typeof schema>;

/**
 * Render the registration page.
 *
 * Replaces the form with a neutral confirmation once the backend accepts the registration. The
 * confirmation never states that an account was created, because the backend does not either.
 *
 * @returns The page element.
 */
export function RegisterPage(): JSX.Element {
  const [accepted, setAccepted] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { username: "", email: "", password: "", password_confirm: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    const result = await registerAccount(values);
    setAccepted(result.email);
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;

  if (accepted !== null) {
    return (
      <Page title="Check your email">
        <Alert kind="success" title="Registration received">
          If the details you entered can be used, an activation link is on its way to {accepted}.
          Open the link to finish creating your account.
        </Alert>
        <ul className="flex flex-col gap-2 text-sm">
          <li>
            <TextLink to={PATHS.resendActivation}>Send the activation email again</TextLink>
          </li>
          <li>
            <TextLink to={PATHS.signIn}>Back to sign in</TextLink>
          </li>
        </ul>
      </Page>
    );
  }
  return (
    <Page title="Create an account">
      <Card>
        <Form label="Create an account" onSubmit={onSubmit}>
          {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
          <TextField
            label="Username"
            autoComplete="username"
            error={errors.username?.message}
            {...register("username")}
          />
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            error={errors.password?.message}
            {...register("password")}
          />
          <TextField
            label="Repeat password"
            type="password"
            autoComplete="new-password"
            error={errors.password_confirm?.message}
            {...register("password_confirm")}
          />
          <Button type="submit" busy={isSubmitting}>
            Create account
          </Button>
        </Form>
      </Card>
      <p className="text-sm">
        Already registered? <TextLink to={PATHS.signIn}>Sign in</TextLink>
      </p>
    </Page>
  );
}
