/**
 * Query client setup.
 *
 * Creates the TanStack Query client every feature shares and names the resource keys. Only network
 * failures are retried; an HTTP error is an answer, not a transient fault.
 *
 * @packageDocumentation
 */

import { QueryClient } from "@tanstack/react-query";

import { NetworkError } from "./errors";

const MAX_NETWORK_RETRIES = 2;
const STALE_TIME_MS = 30_000;

/**
 * Resource keys.
 *
 * One key per backend resource, so invalidation and clearing refer to the same names everywhere.
 */
export const queryKeys = {
  me: ["me"] as const,
};

/**
 * Decide whether a failed query should be retried.
 *
 * Retries only network failures, and only a few times; documented HTTP errors are returned to the
 * caller at once.
 *
 * @param failureCount - Failures so far.
 * @param error - The thrown value.
 * @returns True when the query should run again.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  return error instanceof NetworkError && failureCount < MAX_NETWORK_RETRIES;
}

/**
 * Create the shared query client.
 *
 * Applies the retry policy to queries, disables retries for mutations, and avoids refetching
 * on window focus so a signed-in screen does not flicker.
 *
 * @returns A new client.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: STALE_TIME_MS, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}
