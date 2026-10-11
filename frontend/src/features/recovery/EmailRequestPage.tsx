/**
 * Email request page.
 *
 * The shared screen for asking the backend to email a recovery link. The answer is the same for
 * known and unknown addresses, so the page can never reveal which addresses are registered.
 *
 * @packageDocumentation
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import type { JSX } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { useSubmission } from "../../api";
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
 * Email request page props.
 *
 * The page title, an introduction, the submit label, and the request that returns the backend's
 * acceptance statement.
 */
export interface EmailRequestPageProps {
  readonly title: string;
  readonly intro: string;
  readonly submitLabel: string;
  readonly request: (email: string) => Promise<string>;
}

/**
 * Render an email request page.
 *
 * Shows the backend's statement verbatim after a successful request, clears it when the next
 * attempt starts, and keeps the typed address after a failure so it can be corrected.
 *
 * @param props - The page props.
 * @returns The page element.
 */
export function EmailRequestPage({
  title,
  intro,
  submitLabel,
  request,
}: EmailRequestPageProps): JSX.Element {
  const [statement, setStatement] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "" } });
  const { problem, details, onSubmit } = useSubmission(form, async (values) => {
    setStatement(null);
    setStatement(await request(values.email));
  });
  const {
    register,
    formState: { errors, isSubmitting },
  } = form;
  return (
    <Page title={title}>
      <Card>
        <Form label={title} onSubmit={onSubmit}>
          <p>{intro}</p>
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
            {submitLabel}
          </Button>
        </Form>
      </Card>
      <p className="text-sm">
        <TextLink to={PATHS.signIn}>Back to sign in</TextLink>
      </p>
    </Page>
  );
}
