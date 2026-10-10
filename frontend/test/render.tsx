/**
 * Render helpers for component tests.
 *
 * Wraps a component or the whole application in the providers it needs, with a query client that
 * never retries so failures surface at once instead of after a backoff delay.
 *
 * @packageDocumentation
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import type { JSX, ReactNode } from "react";
import { MemoryRouter } from "react-router";

import { App } from "../src/App";
import { FragmentCredentialsProvider, SessionProvider } from "../src/session";
import type { FragmentCredentials } from "../src/session";

/**
 * Options for rendering the application.
 *
 * The starting path, any credentials read from the fragment, and an existing query client.
 */
export interface AppOptions {
  readonly path?: string;
  readonly credentials?: FragmentCredentials | null;
  readonly client?: QueryClient;
}

/**
 * Result of rendering the application.
 *
 * The Testing Library result, a user-event instance, and the query client in use.
 */
export interface AppRender extends RenderResult {
  readonly user: UserEvent;
  readonly client: QueryClient;
}

/**
 * Create a query client for tests.
 *
 * Disables retries for queries and mutations so an error appears without waiting for a backoff.
 * A failing request therefore shows its error at once.
 *
 * @returns A new client.
 */
export function createTestClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

/**
 * Render the whole application at a path.
 *
 * Uses a memory router so a test can start on any route without touching the real location.
 * The helper returns the client so a test can inspect or seed the cache.
 *
 * @param options - Starting path, credentials, and client.
 * @returns The render result with a user-event instance and the client.
 */
export function renderApp(options: AppOptions = {}): AppRender {
  const client = options.client ?? createTestClient();
  const result = render(
    <MemoryRouter initialEntries={[options.path ?? "/"]}>
      <App credentials={options.credentials ?? null} client={client} />
    </MemoryRouter>,
  );
  return { ...result, user: userEvent.setup(), client };
}

/**
 * Render a component inside the session providers.
 *
 * Provides the query client, fragment credentials, session, and a memory router, so a component
 * that reads the session can be tested without the route table.
 *
 * @param node - The element to render.
 * @param options - Starting path, credentials, and client.
 * @returns The render result with a user-event instance and the client.
 */
export function renderWithSession(node: ReactNode, options: AppOptions = {}): AppRender {
  const client = options.client ?? createTestClient();
  const tree: JSX.Element = (
    <QueryClientProvider client={client}>
      <FragmentCredentialsProvider initial={options.credentials ?? null}>
        <SessionProvider>
          <MemoryRouter initialEntries={[options.path ?? "/"]}>{node}</MemoryRouter>
        </SessionProvider>
      </FragmentCredentialsProvider>
    </QueryClientProvider>
  );
  return { ...render(tree), user: userEvent.setup(), client };
}
