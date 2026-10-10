/**
 * URL fragment credentials.
 *
 * Reads the account key and token that emailed links carry in the URL fragment and removes the
 * fragment from the address bar and history, so the credentials are never kept or logged.
 *
 * @packageDocumentation
 */

/**
 * Credentials from an emailed link.
 *
 * The account key and the single-use token, held only in memory until the request that uses them.
 */
export interface FragmentCredentials {
  readonly account: string;
  readonly token: string;
}

/**
 * The parts of the window this module needs.
 *
 * A narrow view of location and history, so tests can supply a plain object.
 */
export interface FragmentWindow {
  readonly location: Pick<Location, "hash" | "pathname" | "search">;
  readonly history: Pick<History, "replaceState" | "state">;
}

const MAX_CREDENTIAL_LENGTH = 2048;

/**
 * Read and remove fragment credentials.
 *
 * When the fragment carries an account or token key it is removed by replacing the current
 * history entry, whether or not both values are usable. Other fragments are left untouched.
 *
 * @param win - The window, or a stand-in with the same location and history.
 * @returns The credentials when both are present and a sane length, otherwise null.
 */
export function takeFragmentCredentials(win: FragmentWindow): FragmentCredentials | null {
  const parameters = new URLSearchParams(win.location.hash.slice(1));
  if (!parameters.has("account") && !parameters.has("token")) {
    return null;
  }
  win.history.replaceState(win.history.state, "", win.location.pathname + win.location.search);
  const account = parameters.get("account") ?? "";
  const token = parameters.get("token") ?? "";
  const usable =
    account !== "" &&
    token !== "" &&
    account.length <= MAX_CREDENTIAL_LENGTH &&
    token.length <= MAX_CREDENTIAL_LENGTH;
  return usable ? { account, token } : null;
}
