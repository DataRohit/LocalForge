/**
 * CSRF cookie access.
 *
 * Reads the readable CSRF cookie the backend seeds, whose value the client copies into the
 * X-CSRFToken header of every unsafe request. The cookie name is the frozen default.
 *
 * @packageDocumentation
 */

/**
 * CSRF cookie name.
 *
 * The default name frozen in the Phase 11 manifest for the readable double-submit cookie.
 */
export const CSRF_COOKIE_NAME = "__Host-lf-csrf";

/**
 * CSRF request header name.
 *
 * The header Django compares with the cookie value.
 */
export const CSRF_HEADER_NAME = "X-CSRFToken";

/**
 * Read one cookie value from a cookie string.
 *
 * Splits the string into pairs and returns the decoded value of the first pair with the exact
 * name, so a similarly named cookie is never matched.
 *
 * @param name - Cookie name.
 * @param cookies - Cookie string, defaulting to the document's cookies.
 * @returns The decoded value, or null when the cookie is absent or empty.
 */
export function readCookie(name: string, cookies: string = document.cookie): string | null {
  for (const pair of cookies.split(";")) {
    const separator = pair.indexOf("=");
    if (separator !== -1 && pair.slice(0, separator).trim() === name) {
      const value = decodeURIComponent(pair.slice(separator + 1).trim());
      return value === "" ? null : value;
    }
  }
  return null;
}

/**
 * Read the current CSRF token.
 *
 * Reads the frozen cookie name from the document, so callers never handle the name.
 * A null result means the backend has not seeded the cookie yet.
 *
 * @returns The CSRF cookie value, or null when the backend has not seeded it.
 */
export function readCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE_NAME);
}
