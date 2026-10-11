/**
 * Forgot-password page.
 *
 * Asks for the account email and requests a password reset link. The request is accepted with the
 * same statement whether or not the address belongs to an account.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { requestPasswordReset } from "../../api";
import { EmailRequestPage } from "./EmailRequestPage";

/**
 * Request a password reset email.
 *
 * Passes the reset request to the shared email screen and returns the backend's statement.
 *
 * @param email - The address entered.
 * @returns The state-independent acceptance statement.
 */
async function request(email: string): Promise<string> {
  return (await requestPasswordReset({ email })).detail;
}

/**
 * Render the forgot-password page.
 *
 * Supplies the wording and the reset request to the shared email screen.
 * Nothing here depends on whether the address belongs to an account.
 *
 * @returns The page element.
 */
export function ForgotPasswordPage(): JSX.Element {
  return (
    <EmailRequestPage
      title="Forgot your password?"
      intro="Enter the email address of your account. If it can be used, we will send a link to choose a new password."
      submitLabel="Send reset link"
      request={request}
    />
  );
}
