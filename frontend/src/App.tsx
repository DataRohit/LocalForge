/**
 * Application root component.
 *
 * Composes the providers every screen needs, the query client, the fragment credentials, and the
 * session, around the route table. The router is supplied by the caller.
 *
 * @packageDocumentation
 */

import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { JSX } from "react";

import { createQueryClient } from "./api";
import { AppRoutes } from "./routes/AppRoutes";
import { FragmentCredentialsProvider, SessionProvider } from "./session";
import type { FragmentCredentials } from "./session";

/**
 * Application props.
 *
 * The credentials read from the URL fragment at startup, and an optional query client for tests.
 */
export interface AppProps {
  readonly credentials: FragmentCredentials | null;
  readonly client?: QueryClient;
}

/**
 * Render the application.
 *
 * Creates one query client for the lifetime of the page unless the caller supplies one.
 * The query client is created once per page load, so the cache lives exactly as long as the page.
 *
 * @param props - The application props.
 * @returns The root element tree.
 */
export function App({ credentials, client }: AppProps): JSX.Element {
  const [owned] = useState(createQueryClient);
  return (
    <QueryClientProvider client={client ?? owned}>
      <FragmentCredentialsProvider initial={credentials}>
        <SessionProvider>
          <AppRoutes />
        </SessionProvider>
      </FragmentCredentialsProvider>
    </QueryClientProvider>
  );
}
