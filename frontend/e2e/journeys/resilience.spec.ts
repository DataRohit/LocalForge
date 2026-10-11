/**
 * Resilience journeys.
 *
 * Exercises CSRF handling, an expired session, throttling, and loss of the network in a real
 * browser against the testing backend, where a controlled mock could hide a real cookie or header.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { COOKIES, newAccount, seedAccount, signIn } from "../support/flows";
import { mailSettings } from "../support/mail";

const mail = mailSettings();
const ORIGIN = process.env.E2E_HTTPS_ORIGIN ?? "";

test("a missing CSRF cookie is seeded again and the change succeeds", async ({ page, request }) => {
  const account = newAccount("csrf");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page.context().clearCookies({ name: COOKIES.csrf });

  await page.getByLabel("Email", { exact: true }).fill(`new.${account.email}`);
  await page.getByRole("button", { name: "Save email" }).click();

  await expect(page.getByText("Email updated")).toBeVisible();
});

test("a wrong CSRF token is refused and the session survives", async ({ page, request }) => {
  const account = newAccount("tamper");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page
    .context()
    .addCookies([{ name: COOKIES.csrf, value: "tampered", url: ORIGIN, secure: true }]);

  await page.getByLabel("Email", { exact: true }).fill(`new.${account.email}`);
  await page.getByRole("button", { name: "Save email" }).click();
  await expect(page.getByRole("alert")).toContainText("Request refused");
  await expect(page.getByRole("alert")).toContainText("Reload the page, then try again.");

  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Your account" })).toBeVisible();
});

test("an expired access cookie is renewed with the refresh cookie", async ({ page, request }) => {
  const account = newAccount("exp");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page.context().clearCookies({ name: COOKIES.access });

  const refreshed = page.waitForResponse((response) =>
    response.url().includes("/browser/refresh/"),
  );
  await page.reload();

  expect((await refreshed).status()).toBe(204);
  await expect(page.getByRole("heading", { level: 1, name: "Your account" })).toBeVisible();
});

test("losing both cookies returns the person to sign in", async ({ page, request }) => {
  const account = newAccount("lost");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page.context().clearCookies({ name: COOKIES.access });
  await page.context().clearCookies({ name: COOKIES.refresh });

  await page.reload();

  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});

test("repeated resend requests are throttled with a wait time", async ({ page }) => {
  const email = `${newAccount("slow").username}@example.test`;
  await page.goto("/resend-activation");

  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByRole("button", { name: /Send/ }).click();
    await expect(page.getByText("Request received")).toBeVisible();
  }
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByRole("button", { name: /Send/ }).click();

  await expect(page.getByText("Too many attempts")).toBeVisible();
  await expect(page.getByText(/Try again in \d+ seconds/)).toBeVisible();
  await expect(page.getByText("Request received")).toBeHidden();
});

test("losing the network shows the offline notice and keeps what was typed", async ({
  page,
  request,
}) => {
  const account = newAccount("off");
  await seedAccount(request, mail, account);
  await page.goto("/sign-in");
  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.waitForLoadState("networkidle");

  await page.context().setOffline(true);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByText("You appear to be offline. Check your connection and try again."),
  ).toBeVisible();
  await expect(page.getByLabel("Username", { exact: true })).toHaveValue(account.username);

  await page.context().setOffline(false);
  await page.waitForFunction(() => navigator.onLine);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Your account" })).toBeVisible();
});
