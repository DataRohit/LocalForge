/**
 * Account lifecycle journeys.
 *
 * Registers a person, follows the emailed activation link, signs in, edits the profile, signs out,
 * and deletes an account, all through the real interface against the testing backend.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { newAccount, seedAccount, signIn, signOut } from "../support/flows";
import { mailSettings, waitForLink } from "../support/mail";

const mail = mailSettings();

test("a person registers, activates from the email, signs in, edits the profile, and signs out", async ({
  page,
}) => {
  const account = newAccount("life");

  await page.goto("/register");
  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByLabel("Repeat password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Check your email" })).toBeVisible();

  const link = await waitForLink(mail, account.email, "/activate");
  await page.goto(link);
  await expect(page).toHaveURL(/\/activate$/);
  await page.getByRole("button", { name: "Activate account" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Account activated" })).toBeVisible();

  await signIn(page, account);
  await expect(page.getByText(account.username, { exact: true })).toBeVisible();

  const changed = `changed.${account.email}`;
  await page.getByLabel("Email", { exact: true }).fill(changed);
  await page.getByRole("button", { name: "Save email" }).click();
  await expect(page.getByText("Email updated")).toBeVisible();
  await page.reload();
  await expect(page.getByText(changed, { exact: true })).toBeVisible();

  await signOut(page);
  await page.goto("/account");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});

test("a person deletes the account and can no longer sign in", async ({ page, request }) => {
  const account = newAccount("gone");
  await seedAccount(request, mail, account);
  await signIn(page, account);

  await page.getByRole("button", { name: "Delete account" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete your account?" });
  await dialog.getByLabel("Current password", { exact: true }).fill(account.password);
  await dialog.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByText("Account deleted", { exact: true })).toBeVisible();

  await page.getByLabel("Username", { exact: true }).fill(account.username);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("Authentication failed.");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
});
