/**
 * Development proxy origin guard.
 *
 * Resolves the backend origin the Vite development server proxies to and refuses the published
 * development ports, so a local run can never reach the live environment.
 *
 * @packageDocumentation
 */

/**
 * Default backend origin.
 *
 * A host-mode backend running against testing, used when no origin is supplied.
 */
export const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:28000";

const DEVELOPMENT_PORTS: ReadonlySet<string> = new Set(["8000", "8080", "8081"]);

/**
 * Resolve the origin the development server proxies backend routes to.
 *
 * Parses the supplied value or the default and rejects any origin on a port the live development
 * environment publishes, because Phase 12 work must reach the testing backend only.
 *
 * @param value - Origin from the environment, or undefined to use the default.
 * @returns The normalized origin without a path.
 * @throws Error If the value is not a URL or names a development port.
 */
export function resolveBackendOrigin(value: string | undefined): string {
  const origin = new URL(value ?? DEFAULT_BACKEND_ORIGIN);
  if (DEVELOPMENT_PORTS.has(origin.port)) {
    throw new Error(`Port ${origin.port} belongs to the development environment.`);
  }
  return origin.origin;
}
