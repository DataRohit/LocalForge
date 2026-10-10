/**
 * Schema-derived request and response types.
 *
 * Computes each operation's JSON request body and success body from the generated schema types, so
 * a change to the OpenAPI document that is not reflected in callers fails the type check.
 *
 * @packageDocumentation
 */

import type { OperationId } from "./generated/operations";
import type { operations as Spec } from "./generated/schema";

type SuccessStatus = 200 | 201 | 202 | 204;

/**
 * Operation identifier.
 *
 * The operationId of any operation in the committed OpenAPI document.
 */
export type { OperationId };

type Known<Id extends OperationId> = Id & keyof Spec;

/**
 * JSON request body of an operation.
 *
 * The documented application/json body, or undefined for an operation without one.
 */
export type RequestBody<Id extends OperationId> =
  NonNullable<Spec[Known<Id>]["requestBody"]> extends {
    content: { "application/json": infer Body };
  }
    ? Body
    : undefined;

/**
 * Success body of an operation.
 *
 * The union of the documented 2xx JSON bodies, with undefined standing for a bodiless response.
 */
export type SuccessBody<Id extends OperationId> = {
  [
    Status in Extract<keyof Spec[Known<Id>]["responses"], SuccessStatus>
  ]: Spec[Known<Id>]["responses"][Status] extends {
    content: { "application/json": infer Body };
  }
    ? Body
    : undefined;
}[Extract<keyof Spec[Known<Id>]["responses"], SuccessStatus>];
