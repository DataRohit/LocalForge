/**
 * Typed endpoint functions.
 *
 * One small function per operation the interface uses, each resolving through the client with the
 * schema-derived types. Features call these and never build a request themselves.
 *
 * @packageDocumentation
 */

import { z } from "zod";

import { call } from "./client";
import { UnexpectedResponseError } from "./errors";
import type { components } from "./generated/schema";
import type { OperationId, RequestBody, SuccessBody } from "./types";

/**
 * Account profile.
 *
 * The caller's public profile with the capabilities the backend grants.
 */
export type UserProfile = components["schemas"]["UserProfile"];

/**
 * Operations the interface calls.
 *
 * The set the exhaustive status tests iterate over, so every documented status of every exposed
 * operation has a tested outcome.
 */
export const exposedOperations = [
  "browser_csrf",
  "browser_login",
  "browser_refresh",
  "browser_logout",
  "user_registration_or_activation",
  "user_activation_resend",
  "user_password_reset_request",
  "user_password_reset_confirm",
  "user_username_reset_request",
  "user_username_reset_confirm",
  "user_profile_retrieve",
  "user_profile_update",
  "user_profile_delete",
  "user_password_change",
  "user_username_change",
] as const satisfies readonly OperationId[];

const profileSchema = z.object({
  id: z.string(),
  username: z.string(),
  email: z.string(),
  capabilities: z.array(
    z.enum([
      "account:read",
      "account:update",
      "account:delete",
      "credentials:change",
      "notifications:receive",
    ]),
  ),
}) satisfies z.ZodType<UserProfile>;

/**
 * Check a profile body against the documented shape.
 *
 * Guards the one object the whole session depends on, so contract drift fails loudly instead of
 * producing a half-empty session.
 *
 * @param value - Decoded body.
 * @returns The validated profile.
 * @throws UnexpectedResponseError If the body is not a profile.
 */
function toProfile(value: unknown): UserProfile {
  const result = profileSchema.safeParse(value);
  if (!result.success) {
    throw new UnexpectedResponseError("The profile response is not a profile.", 200);
  }
  return result.data;
}

/**
 * Seed the CSRF cookie.
 *
 * Asks the backend to set the readable CSRF cookie. The client does this on its own
 * when an unsafe request finds the cookie missing, so features rarely call it.
 *
 * @param signal - Optional cancellation signal.
 * @returns Nothing; the backend answers 204.
 */
export function seedCsrf(signal?: AbortSignal): Promise<undefined> {
  return call("browser_csrf", signal === undefined ? {} : { signal });
}

/**
 * Sign in with a username and password.
 *
 * Sets the session cookies and returns the profile.
 * Posts the credentials to the browser login route, which sets the HttpOnly session
 * cookies, and validates the returned profile before the session relies on it.
 *
 * @param body - Username and password.
 * @returns The signed-in profile.
 */
export async function login(body: RequestBody<"browser_login">): Promise<UserProfile> {
  return toProfile(await call("browser_login", { body }));
}

/**
 * Sign out.
 *
 * Posts to the browser logout route, which blacklists the refresh token and clears
 * the cookies. It is safe to repeat.
 *
 * @returns Nothing; the backend answers 204 and clears the cookies.
 */
export function logout(): Promise<undefined> {
  return call("browser_logout");
}

/**
 * Read the signed-in profile.
 *
 * Reads the profile route and validates the shape, so a contract change fails here
 * rather than producing a half-empty session.
 *
 * @param signal - Optional cancellation signal.
 * @returns The profile.
 */
export async function fetchProfile(signal?: AbortSignal): Promise<UserProfile> {
  return toProfile(await call("user_profile_retrieve", signal === undefined ? {} : { signal }));
}

/**
 * Update the profile email.
 *
 * Patches the one mutable profile field. Backend validation errors arrive as
 * field errors on the thrown error.
 *
 * @param body - The new email.
 * @returns The updated profile.
 */
export async function updateProfile(
  body: RequestBody<"user_profile_update">,
): Promise<UserProfile> {
  return toProfile(await call("user_profile_update", { body }));
}

/**
 * Delete the account.
 *
 * Deletes the signed-in account after the backend verifies the password. The caller
 * must then clear the session and every cache.
 *
 * @param body - The current password.
 * @returns Nothing; the backend answers 204.
 */
export function deleteAccount(body: RequestBody<"user_profile_delete">): Promise<undefined> {
  return call("user_profile_delete", { body });
}

/**
 * Register an account.
 *
 * Creates an inactive account and always reports the same accepted result, so the
 * response never reveals whether the address or username was already taken.
 *
 * @param body - Registration fields.
 * @returns The accepted username and email.
 * @throws UnexpectedResponseError If the backend answers without the documented body.
 */
export async function register(
  body: components["schemas"]["Registration"],
): Promise<components["schemas"]["RegistrationResponse"]> {
  const result = await call("user_registration_or_activation", { body });
  if (result === undefined) {
    throw new UnexpectedResponseError("The registration response had no body.", 204);
  }
  return result;
}

/**
 * Activate an account with the emailed credentials.
 *
 * Submits the credentials from the emailed link, in the request body only. The four
 * documented token failures arrive as distinct codes on the thrown error.
 *
 * @param body - The account key and token from the link.
 * @returns Nothing; the backend answers 204.
 * @throws UnexpectedResponseError If the backend answers with a body instead of 204.
 */
export async function activate(
  body: components["schemas"]["ActivationConfirmation"],
): Promise<void> {
  const result = await call("user_registration_or_activation", { body });
  if (result !== undefined) {
    throw new UnexpectedResponseError("The activation response had a body.", 201);
  }
}

/**
 * Request a new activation email.
 *
 * Asks for a new activation email. The answer is identical for unknown, inactive,
 * and active accounts, so it cannot be used to discover accounts.
 *
 * @param body - The email address.
 * @returns The state-independent acceptance statement.
 */
export function resendActivation(
  body: RequestBody<"user_activation_resend">,
): Promise<SuccessBody<"user_activation_resend">> {
  return call("user_activation_resend", { body });
}

/**
 * Request a password reset email.
 *
 * Asks for a password reset email. The answer is identical for known and unknown
 * addresses, so it cannot be used to discover accounts.
 *
 * @param body - The email address.
 * @returns The state-independent acceptance statement.
 */
export function requestPasswordReset(
  body: RequestBody<"user_password_reset_request">,
): Promise<SuccessBody<"user_password_reset_request">> {
  return call("user_password_reset_request", { body });
}

/**
 * Confirm a password reset.
 *
 * Sets a new password with the credentials from the emailed link, sent in the
 * body only. Success revokes every existing session of the account.
 *
 * @param body - Account key, token, and the new password twice.
 * @returns Nothing; the backend answers 204.
 */
export function confirmPasswordReset(
  body: RequestBody<"user_password_reset_confirm">,
): Promise<SuccessBody<"user_password_reset_confirm">> {
  return call("user_password_reset_confirm", { body });
}

/**
 * Request a username reset email.
 *
 * Asks for a username reset email. The answer is identical for known and unknown
 * addresses, so it cannot be used to discover accounts.
 *
 * @param body - The email address.
 * @returns The state-independent acceptance statement.
 */
export function requestUsernameReset(
  body: RequestBody<"user_username_reset_request">,
): Promise<SuccessBody<"user_username_reset_request">> {
  return call("user_username_reset_request", { body });
}

/**
 * Confirm a username reset.
 *
 * Sets a new username with the credentials from the emailed link, sent in the
 * body only. A taken username returns a neutral validation error.
 *
 * @param body - Account key, token, and the new username.
 * @returns Nothing; the backend answers 204.
 */
export function confirmUsernameReset(
  body: RequestBody<"user_username_reset_confirm">,
): Promise<SuccessBody<"user_username_reset_confirm">> {
  return call("user_username_reset_confirm", { body });
}

/**
 * Change the password of the signed-in account.
 *
 * Replaces the password after the backend verifies the current one. Success revokes
 * every existing credential and session of the account.
 *
 * @param body - Current password and the new password twice.
 * @returns Nothing; the backend answers 204.
 */
export function changePassword(
  body: RequestBody<"user_password_change">,
): Promise<SuccessBody<"user_password_change">> {
  return call("user_password_change", { body });
}

/**
 * Change the username of the signed-in account.
 *
 * Replaces the username after the backend verifies the password. The backend then
 * emails a confirmation in the background.
 *
 * @param body - Current password and the new username.
 * @returns Nothing; the backend answers 204.
 */
export function changeUsername(
  body: RequestBody<"user_username_change">,
): Promise<SuccessBody<"user_username_change">> {
  return call("user_username_change", { body });
}
