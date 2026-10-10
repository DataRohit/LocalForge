/**
 * API client.
 *
 * The only module that calls fetch. It resolves each call through the generated operation table,
 * adds the CSRF header, parses the error envelope, treats undocumented statuses as failures, and
 * recovers an expired access cookie with one single-flight refresh and one retry.
 *
 * @packageDocumentation
 */

import { CSRF_HEADER_NAME, readCsrfToken } from "./csrf";
import { ApiError, NetworkError, UnexpectedResponseError, parseEnvelope } from "./errors";
import { operations } from "./generated/operations";
import type { OperationId, RequestBody, SuccessBody } from "./types";
import { notifyUnauthorized } from "./unauthorized";

const REQUEST_TIMEOUT_MS = 20_000;
const UNSAFE_METHODS: ReadonlySet<string> = new Set(["post", "put", "patch", "delete"]);

/**
 * Options for one API call.
 *
 * Carries the JSON body the operation documents and an optional cancellation signal.
 */
export interface CallOptions<Id extends OperationId> {
  readonly body?: RequestBody<Id>;
  readonly signal?: AbortSignal;
}

let csrfSeeding: Promise<void> | null = null;
let refreshing: Promise<boolean> | null = null;

/**
 * Parse a Retry-After header.
 *
 * Accepts only a whole number of seconds, which is what the backend sends.
 *
 * @param header - Header value, or null when absent.
 * @returns The seconds to wait, or null when absent or not a whole number.
 */
function parseRetryAfter(header: string | null): number | null {
  return header !== null && /^\d+$/.test(header) ? Number(header) : null;
}

/**
 * Read and decode a response body.
 *
 * Treats an empty body as absent and any non-empty body as JSON.
 *
 * @param response - Fetch response.
 * @returns The decoded value, or undefined for an empty body.
 * @throws UnexpectedResponseError If a non-empty body is not valid JSON.
 */
async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === "") {
    return undefined;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new UnexpectedResponseError("The response body is not valid JSON.", response.status);
  }
}

/**
 * Make the CSRF cookie available.
 *
 * Seeds the cookie with one shared GET when it is absent, so concurrent unsafe requests wait on
 * the same request instead of each making their own.
 *
 * @param signal - Cancellation signal of the request that needs the token.
 * @returns The CSRF token.
 * @throws UnexpectedResponseError If the browser did not store the seeded cookie.
 */
async function ensureCsrfToken(signal: AbortSignal | undefined): Promise<string> {
  let token = readCsrfToken();
  if (token === null) {
    csrfSeeding ??= send("browser_csrf", undefined, signal)
      .then(() => undefined)
      .finally(() => {
        csrfSeeding = null;
      });
    await csrfSeeding;
    token = readCsrfToken();
  }
  if (token === null) {
    throw new UnexpectedResponseError("The browser did not store the security cookie.", null);
  }
  return token;
}

/**
 * Send one request and interpret its response.
 *
 * Applies the timeout, the CSRF header for unsafe methods, and the documented-status rule, and
 * returns the decoded success body or throws a typed error.
 *
 * @param id - Operation identifier.
 * @param body - JSON body, or undefined for an operation without one.
 * @param signal - Optional cancellation signal.
 * @returns The decoded success body.
 * @throws ApiError For a documented error status.
 * @throws NetworkError If no response arrived.
 * @throws UnexpectedResponseError For an undocumented status or a body that is not as documented.
 */
async function send<Id extends OperationId>(
  id: Id,
  body: RequestBody<Id> | undefined,
  signal: AbortSignal | undefined,
): Promise<SuccessBody<Id>> {
  const operation = operations[id];
  const headers = new Headers({ Accept: "application/json" });
  const init: RequestInit = {
    method: operation.method.toUpperCase(),
    headers,
    credentials: "same-origin",
    signal: AbortSignal.any([AbortSignal.timeout(REQUEST_TIMEOUT_MS), ...(signal ? [signal] : [])]),
  };
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(body);
  }
  if (UNSAFE_METHODS.has(operation.method)) {
    headers.set(CSRF_HEADER_NAME, await ensureCsrfToken(signal));
  }

  let response: Response;
  let decoded: unknown;
  try {
    response = await fetch(operation.path, init);
    decoded = await readJson(response);
  } catch (cause) {
    if (cause instanceof UnexpectedResponseError || signal?.aborted === true) {
      throw cause;
    }
    throw new NetworkError(cause);
  }

  const documented: readonly number[] = operation.statuses;
  if (!documented.includes(response.status)) {
    throw new UnexpectedResponseError(
      "The server answered with an undocumented status.",
      response.status,
    );
  }
  if (response.ok) {
    return decoded as SuccessBody<Id>;
  }
  const envelope = parseEnvelope(decoded);
  if (envelope === null) {
    throw new UnexpectedResponseError(
      "The error response is not an error envelope.",
      response.status,
    );
  }
  throw new ApiError(
    response.status,
    envelope,
    parseRetryAfter(response.headers.get("Retry-After")),
  );
}

/**
 * Refresh the browser session once.
 *
 * Shares one refresh request among concurrent callers. A 401 means the session cannot be
 * recovered; any other failure propagates.
 *
 * @returns True when the cookies were refreshed, false when the refresh was rejected with 401.
 */
function refreshSession(): Promise<boolean> {
  refreshing ??= send("browser_refresh", undefined, undefined)
    .then(() => true)
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        return false;
      }
      throw error;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/**
 * Call one documented operation.
 *
 * Sends the request and, for an authenticated operation answered with 401, refreshes the session
 * once and retries once. The backend sets the CSRF cookie at sign-in and removes it with the
 * session, so without it there is nothing to refresh. Failure to recover announces session loss.
 *
 * @param id - Operation identifier from the generated table.
 * @param options - JSON body and cancellation signal.
 * @returns The decoded success body, or undefined for a bodiless success.
 * @throws ApiError For a documented error status.
 * @throws NetworkError If no response arrived.
 * @throws UnexpectedResponseError For an undocumented status or a malformed response.
 */
export async function call<Id extends OperationId>(
  id: Id,
  options: CallOptions<Id> = {},
): Promise<SuccessBody<Id>> {
  try {
    return await send(id, options.body, options.signal);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 || !operations[id].authenticated) {
      throw error;
    }
    if (readCsrfToken() === null || !(await refreshSession())) {
      notifyUnauthorized();
      throw error;
    }
    try {
      return await send(id, options.body, options.signal);
    } catch (retryError) {
      if (retryError instanceof ApiError && retryError.status === 401) {
        notifyUnauthorized();
      }
      throw retryError;
    }
  }
}
