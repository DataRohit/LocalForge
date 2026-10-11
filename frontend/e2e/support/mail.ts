/**
 * Mailpit client for the browser suite.
 *
 * Reads the emails the testing backend sends through the temporary Mailpit container so journeys
 * can follow the emailed activation and recovery links exactly as a person would.
 *
 * @packageDocumentation
 */

/**
 * Mailpit connection.
 *
 * The web API base URL and the basic-auth credentials from the testing env file.
 */
export interface MailSettings {
  readonly baseUrl: string;
  readonly auth: string;
}

/**
 * Read the Mailpit settings from the process environment.
 *
 * The runner sets both values from the testing env file, so the credentials are never written
 * into a test or a document.
 *
 * @param env - Process environment, defaulting to the real one.
 * @returns The connection settings.
 * @throws Error If the runner did not provide them.
 */
export function mailSettings(env: NodeJS.ProcessEnv = process.env): MailSettings {
  const baseUrl = env.E2E_MAILPIT_URL;
  const auth = env.E2E_MAILPIT_AUTH;
  if (baseUrl === undefined || auth === undefined) {
    throw new Error("The end-to-end runner did not provide the Mailpit settings.");
  }
  return { baseUrl, auth };
}

/**
 * Find a credential link in message text.
 *
 * Matches an absolute link to the given application path that carries the account and token in
 * the URL fragment, which is how the backend formats every emailed credential.
 *
 * @param text - Message body.
 * @param pathname - Application path, such as /activate.
 * @returns The link, or null when the text has none.
 */
export function extractLink(text: string, pathname: string): string | null {
  const escaped = pathname.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`https?://[^\\s"'<>]+${escaped}#account=[^\\s"'<>]+`).exec(text);
  return match === null ? null : match[0];
}

/**
 * Call the Mailpit API.
 *
 * @param settings - Connection settings.
 * @param route - API route below the base URL.
 * @param method - HTTP method.
 * @returns The decoded JSON body, or the text of a reply that is not JSON.
 * @throws Error If Mailpit answers with a failure status.
 */
async function mailpit(settings: MailSettings, route: string, method = "GET"): Promise<unknown> {
  const response = await fetch(`${settings.baseUrl}${route}`, {
    method,
    headers: { Authorization: `Basic ${Buffer.from(settings.auth).toString("base64")}` },
  });
  if (!response.ok) {
    throw new Error(`Mailpit ${method} ${route} answered ${String(response.status)}.`);
  }
  const text = await response.text();
  return (response.headers.get("Content-Type") ?? "").includes("json")
    ? (JSON.parse(text) as unknown)
    : text;
}

/**
 * Wait for an emailed credential link.
 *
 * Polls the messages addressed to a recipient, newest first, until one carries a link to the path.
 * Mail sent inside the request is already stored, so the first poll normally succeeds.
 *
 * @param settings - Connection settings.
 * @param recipient - Email address the message was sent to.
 * @param pathname - Application path of the link.
 * @param attempts - Polls before giving up.
 * @returns The link.
 * @throws Error If no message carries the link in time.
 */
export async function waitForLink(
  settings: MailSettings,
  recipient: string,
  pathname: string,
  attempts = 40,
): Promise<string> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const found = (await mailpit(
      settings,
      `/api/v1/search?query=${encodeURIComponent(`to:${recipient}`)}`,
    )) as { messages?: { ID: string }[] };
    for (const message of found.messages ?? []) {
      const detail = (await mailpit(settings, `/api/v1/message/${message.ID}`)) as {
        Text?: string;
      };
      const link = extractLink(detail.Text ?? "", pathname);
      if (link !== null) {
        return link;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`No ${pathname} email arrived for ${recipient}.`);
}

/**
 * Delete every message.
 *
 * Empties the mailbox before and after a run so no captured email outlives the suite that
 * created it.
 *
 * @param settings - Connection settings.
 */
export async function clearMailbox(settings: MailSettings): Promise<void> {
  await mailpit(settings, "/api/v1/messages", "DELETE");
}
