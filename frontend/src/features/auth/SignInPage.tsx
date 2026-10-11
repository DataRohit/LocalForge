/**
 * Sign-in page.
 *
 * Collects a username and password, signs in through the browser login route, and hands the
 * returned profile to the session. A wrong credential is reported without saying which part.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { login, useSubmission } from "../../api";
import { PATHS, useSessionActions, useSignInNotice } from "../../session";
import type { SignInNotice } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { TextLink } from "../../ui/TextLink";

const schema = z.object({
  username: z.string().min(1, "Enter your username."),
  password: z.string().min(1, "Enter your password."),
});

type Values = z.infer<typeof schema>;

const NOTICES: Readonly<
  Record<
    SignInNotice,
    { readonly kind: "success" | "info"; readonly title: string; readonly text: string }
  >
> = {
  "password-changed": {
    kind: "success",
    title: "Password changed",
    text: "You were signed out everywhere. Sign in with your new password.",
  },
  "session-ended": {
    kind: "info",
    title: "Your session ended",
    text: "Sign in again to continue.",
  },
};

/**
 * Render the sign-in page.
 *
 * On success the session becomes signed in and the public-only guard moves the visitor to the
 * account page. Failures keep what was typed so the person can correct it.
 *
 * @returns The page element.
 */
export function SignInPage(): JSX.Element {
  const actions = useSessionActions();
  const notice = useSignInNotice();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { username: "", password: "" },
  });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    actions.establish(await login(values));
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Page title="Sign in">
      {notice === null ? null : (
        <Alert kind={NOTICES[notice].kind} title={NOTICES[notice].title}>
          {NOTICES[notice].text}
        </Alert>
      )}
      <Card>
        <Form label="Sign in" onSubmit={onSubmit}>
          {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
          <TextField
            label="Username"
            autoComplete="username"
            error={errors.username?.message}
            {...register("username")}
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="current-password"
            error={errors.password?.message}
            {...register("password")}
          />
          <Button type="submit" busy={isSubmitting}>
            Sign in
          </Button>
        </Form>
      </Card>
      <ul className="flex flex-col gap-2 text-sm">
        <li>
          <TextLink to={PATHS.forgotPassword}>Forgot your password?</TextLink>
        </li>
        <li>
          <TextLink to={PATHS.forgotUsername}>Forgot your username?</TextLink>
        </li>
        <li>
          <TextLink to={PATHS.register}>Create an account</TextLink>
        </li>
        <li>
          <TextLink to={PATHS.resendActivation}>Resend the activation email</TextLink>
        </li>
      </ul>
    </Page>
  );
}
