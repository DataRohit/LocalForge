/**
 * Console capture for the test suite.
 *
 * Collects console.error and console.warn calls so the shared setup can fail any test that
 * logs one. A test that expects a log consumes it explicitly through takeConsoleCalls.
 *
 * @packageDocumentation
 */

const captured: string[] = [];

/**
 * Record one console call.
 *
 * Formats the arguments to a single line and appends it to the pending list.
 * The shared teardown fails the test if the list is still non-empty.
 *
 * @param level - Console method that was called.
 * @param args - Arguments the caller passed.
 */
export function recordConsoleCall(level: "error" | "warn", args: readonly unknown[]): void {
  captured.push(`console.${level}: ${args.map(String).join(" ")}`);
}

/**
 * Return and clear the pending console calls.
 *
 * A test that deliberately triggers a log calls this to assert on it, which stops the shared
 * teardown from failing the test.
 *
 * @returns The calls recorded since the last take.
 */
export function takeConsoleCalls(): string[] {
  return captured.splice(0, captured.length);
}
