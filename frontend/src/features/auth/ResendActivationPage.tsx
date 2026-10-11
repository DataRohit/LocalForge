/**
 * Resend-activation page.
 *
 * Asks the backend to send a new activation email. The answer is the same for unknown, inactive,
 * and active accounts, so the page can never be used to discover which addresses are registered.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { resendActivation, useSubmission } from "../../api";
import { PATHS } from "../../session";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Form } from "../../ui/Form";
import { Page } from "../../ui/Page";
import { ProblemAlert } from "../../ui/ProblemAlert";
import { TextField } from "../../ui/TextField";
import { TextLink } from "../../ui/TextLink";

const schema = z.object({ email: z.string().min(1, "Enter your email address.") });

type Values = z.infer<typeof schema>;

/**
 * Render the resend-activation page.
 *
 * Shows the backend's state-independent acceptance statement after a successful request.
 * The statement is the same for unknown, inactive, and active accounts, so it is shown verbatim.
 *
 * @returns The page element.
 */
export function ResendActivationPage(): JSX.Element {
  const [statement, setStatement] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "" } });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    setStatement(null);
    const result = await resendActivation(values);
    setStatement(result.detail);
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Page title="Resend the activation email">
      <Card>
        <Form label="Resend the activation email" onSubmit={onSubmit}>
          {problem === null ? null : <ProblemAlert problem={problem} details={details} />}
          {statement === null ? null : (
            <Alert kind="success" title="Request received">
              {statement}
            </Alert>
          )}
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <Button type="submit" busy={isSubmitting}>
            Send activation email
          </Button>
        </Form>
      </Card>
      <p className="text-sm">
        <TextLink to={PATHS.signIn}>Back to sign in</TextLink>
      </p>
    </Page>
  );
}
