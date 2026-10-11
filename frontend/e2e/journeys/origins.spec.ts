/**
 * Cookie origin journeys.
 *
 * Signs in through the real interface on the plain-HTTP loopback origin and proves the support
 * table: Chromium and Firefox keep the prefixed secure cookies there, while WebKit rejects the
 * session cookies and cannot hold a session, which is why WebKit is supported on HTTPS only.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { COOKIES, newAccount, seedAccount } from "../support/flows";
import { mailSettings } from "../support/mail";

const mail = mailSettings();
const HTTP_ORIGIN = process.env.E2E_HTTP_ORIGIN ?? "";

test("sign-in over plain HTTP follows the browser support table", async ({
  page,
  request,
  browserName,
}) => {
  const account = newAccount("origin");
  await seedAccount(request, mail, account);

  await page.goto(`${HTTP_ORIGIN}/sign-in`);
  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  const heading = page.getByRole("heading", { level: 1 });
  if (browserName === "webkit") {
    await expect(heading).toHaveText("Sign in");
    await page.waitForLoadState("networkidle");
  } else {
    await expect(heading).toHaveText("Your account");
  }
  const names = (await page.context().cookies()).map((cookie) => cookie.name);
  expect(names.includes(COOKIES.access)).toBe(browserName !== "webkit");
  expect(names.includes(COOKIES.refresh)).toBe(browserName !== "webkit");
});
