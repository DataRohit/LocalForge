/**
 * Session module surface.
 *
 * Re-exports the provider, the hooks, the session types, and the fragment credential helpers that
 * features and routes use.
 *
 * @packageDocumentation
 */

export type { Capability, Session, SessionActions, SignInNotice } from "./context";
export { FragmentCredentialsProvider, useFragmentCredentials } from "./FragmentCredentialsContext";
export { takeFragmentCredentials } from "./fragment";
export { PATHS } from "./paths";
export type { FragmentCredentials } from "./fragment";
export { SessionProvider } from "./SessionProvider";
export { useSession, useSessionActions, useSignedInSession, useSignInNotice } from "./useSession";
export type { SignedInSession } from "./useSession";
