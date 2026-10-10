/**
 * API error types.
 *
 * Defines the error classes the client throws and the parser for the shared error envelope.
 * Callers branch on the class, never on message text.
 *
 * @packageDocumentation
 */

import { z } from "zod";

import type { components } from "./generated/schema";

/**
 * Shared error envelope.
 *
 * The body of every documented non-2xx response, as defined by the OpenAPI document.
 */
export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];

const envelopeSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.string(), z.array(z.string())),
  request_id: z.string(),
}) satisfies z.ZodType<ErrorEnvelope>;

/**
 * Parse an unknown value as the shared error envelope.
 *
 * Accepts only objects with a code, message, per-field details, and request identifier, so
 * a proxy page or truncated body is never mistaken for a backend error.
 *
 * @param value - Decoded response body.
 * @returns The envelope, or null when the value does not match.
 */
export function parseEnvelope(value: unknown): ErrorEnvelope | null {
  const result = envelopeSchema.safeParse(value);
  return result.success ? result.data : null;
}

/**
 * Documented HTTP error.
 *
 * Raised for a non-2xx status the schema documents, carrying the parsed envelope and any
 * Retry-After value so callers can show field errors and wait times.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Readonly<Record<string, readonly string[]>>;
  readonly requestId: string;
  readonly retryAfterSeconds: number | null;

  /**
   * Create an error from a parsed envelope.
   *
   * Copies the envelope fields onto the instance and uses the envelope message as the message.
   *
   * @param status - HTTP status code.
   * @param envelope - Parsed error envelope.
   * @param retryAfterSeconds - Parsed Retry-After seconds, or null when absent.
   */
  constructor(status: number, envelope: ErrorEnvelope, retryAfterSeconds: number | null) {
    super(envelope.message);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope.code;
    this.details = envelope.details;
    this.requestId = envelope.request_id;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/**
 * Network failure.
 *
 * Raised when no HTTP response arrived, which the interface treats as being offline.
 * Timeouts and refused connections both land here; a caller abort does not.
 */
export class NetworkError extends Error {
  /**
   * Create a network error.
   *
   * Keeps the underlying failure as the cause for diagnostics.
   *
   * @param cause - The error the platform fetch raised.
   */
  constructor(cause: unknown) {
    super("The network request failed.", { cause });
    this.name = "NetworkError";
  }
}

/**
 * Unexpected response.
 *
 * Raised for a status the schema does not document, a non-envelope error body, or a missing
 * security cookie. It is never silently ignored.
 */
export class UnexpectedResponseError extends Error {
  readonly status: number | null;

  /**
   * Create an unexpected-response error.
   *
   * @param reason - Short description of what was unexpected.
   * @param status - HTTP status code, or null when no response was involved.
   */
  constructor(reason: string, status: number | null) {
    super(reason);
    this.name = "UnexpectedResponseError";
    this.status = status;
  }
}
