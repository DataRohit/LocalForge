/**
 * Fragment credentials context.
 *
 * Holds the credentials read from the URL at startup in memory only, hands them to the screen
 * that needs them, and forgets them once they have been used.
 *
 * @packageDocumentation
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { JSX, ReactNode } from "react";

import type { FragmentCredentials } from "./fragment";

/**
 * What a screen receives.
 *
 * The credentials, or null when there are none, and a function that forgets them.
 */
export interface FragmentCredentialsValue {
  readonly credentials: FragmentCredentials | null;
  readonly consume: () => void;
}

const FragmentCredentialsContext = createContext<FragmentCredentialsValue | null>(null);

/**
 * Provider props.
 *
 * The credentials captured at startup, and the subtree that may read them.
 */
export interface FragmentCredentialsProviderProps {
  readonly initial: FragmentCredentials | null;
  readonly children: ReactNode;
}

/**
 * Provide fragment credentials.
 *
 * Stores the initial credentials in state so consuming them re-renders the screens that read them.
 * A used token is forgotten at once, so it cannot be submitted twice.
 *
 * @param props - The provider props.
 * @returns The provider element.
 */
export function FragmentCredentialsProvider({
  initial,
  children,
}: FragmentCredentialsProviderProps): JSX.Element {
  const [credentials, setCredentials] = useState(initial);
  const consume = useCallback(() => {
    setCredentials(null);
  }, []);
  const value = useMemo(() => ({ credentials, consume }), [credentials, consume]);
  return (
    <FragmentCredentialsContext.Provider value={value}>
      {children}
    </FragmentCredentialsContext.Provider>
  );
}

/**
 * Read the fragment credentials.
 *
 * Reads the context the provider supplies, so a screen never touches the window.
 * The credentials exist in memory only and are forgotten once consumed.
 *
 * @returns The credentials and the function that forgets them.
 * @throws Error If called outside the provider.
 */
export function useFragmentCredentials(): FragmentCredentialsValue {
  const value = useContext(FragmentCredentialsContext);
  if (value === null) {
    throw new Error("useFragmentCredentials needs a FragmentCredentialsProvider.");
  }
  return value;
}
