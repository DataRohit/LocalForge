/**
 * Cookie control for tests.
 *
 * jsdom drops prefixed Secure cookies on a plain-HTTP origin, so tests replace the document cookie
 * string with a controlled value and restore the real property afterwards.
 *
 * @packageDocumentation
 */

import { vi } from "vitest";

let current = "";

/**
 * Set the cookie string the document reports.
 *
 * Replaces the document cookie getter until the shared teardown restores the real property,
 * so no test leaks a cookie value into the next.
 *
 * @param value - Cookie string such as "a=1; b=2".
 */
export function setCookies(value: string): void {
  current = value;
  vi.spyOn(document, "cookie", "get").mockImplementation(() => current);
}
