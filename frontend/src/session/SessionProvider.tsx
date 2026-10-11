/**
 * Session provider.
 *
 * Reads the signed-in profile through the query client, announces loading and error states,
 * clears every cache when the API client reports session loss, and exposes the session actions.
 *
 * @packageDocumentation
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import type { JSX, ReactNode } from "react";

import {
  ApiError,
  fetchProfile,
  logout,
  queryKeys,
  subscribeUnauthorized,
  toProblem,
} from "../api";
import type { Problem, UserProfile } from "../api";
import { SessionContext } from "./context";
import type { Capability, Session, SessionContextValue, SignInNotice } from "./context";

/**
 * Provider props.
 *
 * The subtree that may read the session.
 */
export interface SessionProviderProps {
  readonly children: ReactNode;
}

/**
 * Load the profile, treating a 401 as signed out.
 *
 * @param signal - Cancellation signal from the query.
 * @returns The profile, or null when there is no session.
 */
async function loadProfile(signal: AbortSignal): Promise<UserProfile | null> {
  try {
    return await fetchProfile(signal);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}

/**
 * Forget the signed-in account and everything cached for it.
 *
 * Marks the profile as absent and removes every other cached query, so no data from the previous
 * account can appear for the next one.
 *
 * @param client - The shared query client.
 */
function forgetAccount(client: QueryClient): void {
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== queryKeys.me[0] });
  client.setQueryData(queryKeys.me, null);
}

/**
 * Provide the session.
 *
 * Derives the session state from the profile query and keeps it in step with session loss
 * announced by the API client.
 *
 * @param props - The provider props.
 * @returns The provider element.
 */
export function SessionProvider({ children }: SessionProviderProps): JSX.Element {
  const client = useQueryClient();
  const [notice, setNotice] = useState<SignInNotice | null>(null);
  const query = useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => loadProfile(signal),
    staleTime: Infinity,
  });

  useEffect(
    () =>
      subscribeUnauthorized(() => {
        const current = client.getQueryData<UserProfile | null>(queryKeys.me);
        forgetAccount(client);
        if (current !== undefined && current !== null) {
          setNotice("session-ended");
        }
      }),
    [client],
  );

  const value = useMemo<SessionContextValue>(() => {
    let session: Session;
    if (query.isError) {
      const problem: Problem = toProblem(query.error);
      session = {
        status: "error",
        problem,
        retry: () => {
          void query.refetch();
        },
      };
    } else if (query.data === undefined) {
      session = { status: "loading" };
    } else if (query.data === null) {
      session = { status: "signedOut" };
    } else {
      const granted: ReadonlySet<Capability> = new Set(query.data.capabilities);
      session = {
        status: "signedIn",
        profile: query.data,
        can: (capability) => granted.has(capability),
      };
    }
    return {
      session,
      actions: {
        establish: (profile) => {
          setNotice(null);
          client.setQueryData(queryKeys.me, profile);
        },
        end: async () => {
          try {
            await logout();
          } catch (error) {
            return toProblem(error);
          }
          forgetAccount(client);
          return null;
        },
        drop: (reason) => {
          setNotice(reason ?? null);
          forgetAccount(client);
        },
        refresh: () => client.invalidateQueries({ queryKey: queryKeys.me }),
      },
      notice,
    };
  }, [client, notice, query]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
