/**
 * Journey helpers.
 *
 * Creates uniquely named accounts, seeds active accounts through the real API with a captured
 * email, and drives the sign-in and sign-out screens, so each journey states only what it tests.
 * Signs out remaining sessions before teardown so their live sockets close normally.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

import { waitForLink, type MailSettings } from "./mail.ts";

test.afterEach(async ({ context }) => {
  for (const page of context.pages()) {
    if (!page.isClosed() && (await page.getByRole("button", { name: "Sign out" }).isVisible())) {
      await signOut(page);
      break;
    }
  }
});

/**
 * Account under test.
 *
 * A unique username and email with a password the backend's validators accept.
 */
export interface Account {
  username: string;
  email: string;
  password: string;
}

/**
 * Cookie names.
 *
 * The frozen defaults for the access, refresh, and CSRF cookies.
 */
export const COOKIES = {
  access: "__Host-lf-access",
  refresh: "__Secure-lf-refresh",
  csrf: "__Host-lf-csrf",
} as const;

/**
 * Password used for new accounts.
 *
 * Long, unrelated to any generated name, and not on any common list.
 */
export const PASSWORD = "Violet-Harbor-Lantern-4172!";

/**
 * Replacement password for recovery journeys.
 *
 * Also long and unrelated to any generated name, so the backend's validators accept it.
 */
export const NEW_PASSWORD = "Granite-Meadow-Compass-8305!";

/**
 * Create an account description with a unique name.
 *
 * Combines a short tag, the time, and random characters, so parallel tests and repeated runs never
 * collide in the shared database.
 *
 * @param tag - Short label naming the journey.
 * @returns The new account description; nothing is created yet.
 */
export function newAccount(tag: string): Account {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
  const username = `${tag}${suffix}`;
  return { username, email: `${username}@example.test`, password: PASSWORD };
}

/**
 * Read the account and token from an emailed link.
 *
 * Parses the URL fragment, which is where the backend puts both credentials so no server or
 * proxy ever receives them.
 *
 * @param link - Absolute link with a fragment.
 * @returns The two credentials.
 */
export function fragmentOf(link: string): { account: string; token: string } {
  const params = new URLSearchParams(new URL(link).hash.slice(1));
  return { account: params.get("account") ?? "", token: params.get("token") ?? "" };
}

/**
 * Register and activate an account through the API.
 *
 * Posts the registration, reads the activation email from Mailpit, and posts the activation, so a
 * journey that is not about registration starts with a ready account.
 *
 * @param request - API context with the application's base URL.
 * @param mail - Mailpit settings.
 * @param account - The account to create.
 */
export async function seedAccount(
  request: APIRequestContext,
  mail: MailSettings,
  account: Account,
): Promise<void> {
  const created = await request.post("/api/v1/users/", {
    data: {
      username: account.username,
      email: account.email,
      password: account.password,
      password_confirm: account.password,
    },
  });
  expect(created.status()).toBe(201);
  const credentials = fragmentOf(await waitForLink(mail, account.email, "/activate"));
  const activated = await request.post("/api/v1/users/", { data: credentials });
  expect(activated.status()).toBe(204);
}

/**
 * Sign in through the sign-in screen.
 *
 * Opens the sign-in page and submits the form, so a journey states only whose credentials it uses.
 * The account page it waits for proves the browser accepted the session cookies.
 *
 * @param page - The page to drive.
 * @param account - Credentials to submit.
 */
export async function signIn(page: Page, account: Account): Promise<void> {
  await page.goto("/sign-in");
  await submitSignIn(page, account);
}

/**
 * Fill and submit the sign-in form on the current page.
 *
 * Used after following a link to the sign-in screen, and by signIn once it has opened the page.
 * Waits for the account page, which proves the browser accepted the session cookies.
 *
 * @param page - The page showing the sign-in form.
 * @param account - Credentials to submit.
 */
export async function submitSignIn(page: Page, account: Account): Promise<void> {
  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Your account" })).toBeVisible();
}

/**
 * Sign out through the shell button.
 *
 * Clicks the button and waits for the sign-in page, which proves the session ended in the
 * interface.
 *
 * @param page - The page to drive.
 */
export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
}

/**
 * Read the value of a cookie in the page's browser context.
 *
 * Includes HttpOnly cookies, which the page itself cannot read, so a test can assert on them
 * without exposing them to script.
 *
 * @param page - The page whose context is read.
 * @param name - Cookie name.
 * @returns The value, or undefined when absent.
 */
export async function cookieValue(page: Page, name: string): Promise<string | undefined> {
  const cookies = await page.context().cookies();
  return cookies.find((cookie) => cookie.name === name)?.value;
}
