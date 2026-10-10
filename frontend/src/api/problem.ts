/**
 * Error presentation model.
 *
 * Turns any error the client can throw into one small shape the interface renders: a kind to
 * branch on, a message, per-field errors, and the wait time for throttling.
 *
 * @packageDocumentation
 */

import { ApiError, NetworkError } from "./errors";

/**
 * Category of a failure.
 *
 * Lets a screen choose its outcome without inspecting status codes.
 */
export type ProblemKind =
  | "offline"
  | "throttled"
  | "validation"
  | "unauthorized"
  | "forbidden"
  | "notFound"
  | "server"
  | "unexpected"
  | "other";

/**
 * A failure ready to render.
 *
 * Holds the category, the message to show, the backend's per-field errors, and the identifiers
 * a person can quote when reporting the problem.
 */
export interface Problem {
  readonly kind: ProblemKind;
  readonly message: string;
  readonly fields: Readonly<Record<string, readonly string[]>>;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;
}

const OFFLINE_MESSAGE = "You appear to be offline. Check your connection and try again.";
const UNEXPECTED_MESSAGE = "Something unexpected happened. Try again in a moment.";
const SERVER_MESSAGE = "The service is unavailable right now. Try again in a moment.";

/**
 * Classify a documented HTTP status.
 *
 * @param status - HTTP status code.
 * @returns The problem kind for that status.
 */
function kindOf(status: number): ProblemKind {
  if (status === 429) {
    return "throttled";
  }
  if (status === 400) {
    return "validation";
  }
  if (status === 401) {
    return "unauthorized";
  }
  if (status === 403) {
    return "forbidden";
  }
  if (status === 404) {
    return "notFound";
  }
  return status >= 500 ? "server" : "other";
}

/**
 * Convert an error to a renderable problem.
 *
 * Maps API errors by status and network failures to offline, and treats everything else as
 * unexpected, so a screen never handles a raw exception. Server failures use fixed wording.
 *
 * @param error - The thrown value.
 * @returns The problem to show.
 */
export function toProblem(error: unknown): Problem {
  if (error instanceof ApiError) {
    const kind = kindOf(error.status);
    return {
      kind,
      message: kind === "server" ? SERVER_MESSAGE : error.message,
      fields: error.details,
      code: error.code,
      requestId: error.requestId,
      retryAfterSeconds: error.retryAfterSeconds,
    };
  }
  const base = { fields: {}, code: null, requestId: null, retryAfterSeconds: null } as const;
  if (error instanceof NetworkError) {
    return { ...base, kind: "offline", message: OFFLINE_MESSAGE };
  }
  return { ...base, kind: "unexpected", message: UNEXPECTED_MESSAGE };
}
