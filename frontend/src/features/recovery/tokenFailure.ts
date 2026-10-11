/**
 * Recovery token failures.
 *
 * Maps the documented token error codes of the password and username reset flows to messages. A
 * code in these families means the emailed link cannot be reused, whatever the person types next.
 *
 * @packageDocumentation
 */

/**
 * Reset flow family.
 *
 * Names the error code prefix: password_reset_token_* or username_reset_token_*.
 */
export type TokenFamily = "password" | "username";

const MESSAGES: Readonly<Record<string, string>> = {
  expired: "This link has expired. Request a new one.",
  used: "This link was already used. Request a new one if you still need it.",
  foreign: "This link is not valid. Request a new one.",
  malformed: "This link is not valid. Request a new one.",
};

/**
 * Explain a token failure.
 *
 * Returns a message only for the four documented token codes of the family, so a field
 * validation failure is never mistaken for a dead link.
 *
 * @param family - The reset flow that raised the error.
 * @param code - The backend error code, or null when there is none.
 * @returns The message, or null when the code is not a token failure of this family.
 */
export function tokenFailureMessage(family: TokenFamily, code: string | null): string | null {
  const prefix = `${family}_reset_token_`;
  return code?.startsWith(prefix) === true ? (MESSAGES[code.slice(prefix.length)] ?? null) : null;
}
