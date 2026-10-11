/**
 * Live notification journeys.
 *
 * Opens the notification feed over the cookie-authenticated socket and proves a real backend event
 * reaches it, and that ending the session elsewhere ends the feed here.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { newAccount, seedAccount, signIn, signOut } from "../support/flows";
import { mailSettings } from "../support/mail";

const mail = mailSettings();

test("a username change arrives on the open notification feed", async ({ page, request }) => {
  const account = newAccount("live");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page.goto("/notifications");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await expect(page.getByText(/No notifications yet/)).toBeVisible();

  const other = await page.context().newPage();
  await other.goto("/account/username");
  await other.getByLabel("New username", { exact: true }).fill(`${account.username}z`);
  await other.getByLabel("Current password", { exact: true }).fill(account.password);
  await other.getByRole("button", { name: "Change username" }).click();
  await expect(other.getByText("Username changed", { exact: true })).toBeVisible();

  await expect(page.getByText("Your username was changed.").first()).toBeVisible();
});

test("signing out in another tab ends the live feed", async ({ page, request }) => {
  const account = newAccount("tabs");
  await seedAccount(request, mail, account);
  await signIn(page, account);
  await page.goto("/notifications");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  const other = await page.context().newPage();
  await other.goto("/account");
  await signOut(other);

  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});
