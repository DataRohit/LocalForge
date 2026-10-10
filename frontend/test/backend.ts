/**
 * Mock backend for tests.
 *
 * An MSW server whose handlers take method and path from the generated operation table and refuse
 * statuses the OpenAPI document does not list, so the mock cannot drift from the committed schema.
 *
 * @packageDocumentation
 */

import { http, HttpResponse, type HttpHandler } from "msw";
import { setupServer } from "msw/node";

import { operations, type OperationId } from "../src/api/generated/operations";
import type { components } from "../src/api/generated/schema";
import type { SuccessBody } from "../src/api/types";

/**
 * Shared mock server.
 *
 * Started and reset by the shared test setup; tests add handlers with the helpers below.
 */
export const server = setupServer();

/**
 * Error envelope body.
 *
 * The shape of every documented error response.
 */
export type Envelope = components["schemas"]["ErrorEnvelope"];

/**
 * Build an error envelope.
 *
 * Uses a fixed request identifier so tests can assert on the whole envelope.
 * Details default to empty, matching a non-validation error.
 *
 * @param code - Stable machine-readable code.
 * @param message - Human-readable message.
 * @param details - Per-field errors.
 * @returns The envelope.
 */
export function envelope(
  code: string,
  message: string,
  details: Record<string, string[]> = {},
): Envelope {
  return { code, message, details, request_id: "00000000-0000-4000-8000-000000000000" };
}

/**
 * Throw unless a status is documented for an operation.
 *
 * @param id - Operation identifier.
 * @param status - Status the mock is about to return.
 * @throws Error If the schema does not list the status.
 */
function assertDocumented(id: OperationId, status: number): void {
  const documented: readonly number[] = operations[id].statuses;
  if (!documented.includes(status)) {
    throw new Error(`${id} does not document status ${String(status)}.`);
  }
}

/**
 * Build a handler for one operation.
 *
 * Takes the method and path from the generated operation table and refuses a status
 * the schema does not list, so a mock can never invent a response.
 *
 * @param id - Operation identifier.
 * @param status - Documented status to return.
 * @param body - JSON body, or undefined for no body.
 * @param headers - Extra response headers.
 * @param onRequest - Optional observer of the incoming request.
 * @returns The MSW handler.
 */
export function handler(
  id: OperationId,
  status: number,
  body?: unknown,
  headers: Record<string, string> = {},
  onRequest?: (request: Request) => void,
): HttpHandler {
  assertDocumented(id, status);
  const operation = operations[id];
  return http[operation.method](operation.path, ({ request }) => {
    onRequest?.(request);
    return body === undefined
      ? new HttpResponse(null, { status, headers })
      : HttpResponse.json(body, { status, headers });
  });
}

/**
 * One scripted response.
 *
 * A documented status with an optional JSON body, used to script consecutive answers.
 */
export interface Step {
  readonly status: number;
  readonly body?: unknown;
}

/**
 * Answer an operation with a different response on each call.
 *
 * Returns the nth step on the nth call and repeats the last step afterwards. The handler records
 * how many times it was called.
 *
 * @param id - Operation identifier.
 * @param steps - Responses in call order.
 * @returns A function that reports the number of calls so far.
 */
export function sequence(id: OperationId, steps: readonly Step[]): () => number {
  let calls = 0;
  for (const step of steps) {
    assertDocumented(id, step.status);
  }
  const operation = operations[id];
  server.use(
    http[operation.method](operation.path, () => {
      const step = steps[Math.min(calls, steps.length - 1)];
      calls += 1;
      if (step === undefined) {
        throw new Error("sequence needs at least one step.");
      }
      return step.body === undefined
        ? new HttpResponse(null, { status: step.status })
        : HttpResponse.json(step.body, { status: step.status });
    }),
  );
  return () => calls;
}

/**
 * Answer an operation with a documented success.
 *
 * Registers a handler for a documented success status. The body type comes from the
 * schema, so a wrong shape fails the type check.
 *
 * @param id - Operation identifier.
 * @param status - Documented success status.
 * @param body - The success body the schema documents.
 */
export function succeed<Id extends OperationId>(
  id: Id,
  status: 200 | 201 | 202 | 204,
  body?: SuccessBody<Id>,
): void {
  server.use(handler(id, status, body));
}

/**
 * Answer an operation with a documented error envelope.
 *
 * Registers a handler for a documented error status with an error envelope body.
 * A default envelope is used when the test does not care about the content.
 *
 * @param id - Operation identifier.
 * @param status - Documented error status.
 * @param error - The envelope to return.
 * @param headers - Extra response headers, such as Retry-After.
 */
export function fail(
  id: OperationId,
  status: number,
  error: Envelope = envelope("error", "Failure."),
  headers: Record<string, string> = {},
): void {
  server.use(handler(id, status, error, headers));
}

/**
 * Profile fixture.
 *
 * Returns the same profile every time unless fields are replaced, so tests compare
 * against a stable value.
 *
 * @param overrides - Fields to replace.
 * @returns A profile holding every capability.
 */
export function profile(
  overrides: Partial<components["schemas"]["UserProfile"]> = {},
): components["schemas"]["UserProfile"] {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    username: "ada",
    email: "ada@example.test",
    capabilities: [
      "account:read",
      "account:update",
      "account:delete",
      "credentials:change",
      "notifications:receive",
    ],
    ...overrides,
  };
}
