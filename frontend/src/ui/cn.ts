/**
 * Class name helper.
 *
 * Joins the truthy class names it is given, so components compose conditional Tailwind classes
 * in one readable expression.
 *
 * @packageDocumentation
 */

/**
 * Join class names.
 *
 * Keeps non-empty strings in order and drops false, null, undefined, and empty values.
 * Tailwind classes are never merged or deduplicated; the caller keeps them non-conflicting.
 *
 * @param values - Class names or falsy placeholders.
 * @returns The class names separated by single spaces.
 */
export function cn(...values: readonly (string | false | null | undefined)[]): string {
  return values
    .filter((value): value is string => typeof value === "string" && value !== "")
    .join(" ");
}
