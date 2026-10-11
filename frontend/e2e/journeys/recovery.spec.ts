/**
 * Recovery journeys.
 *
 * Recovers a password and a username through the emailed links, then proves the new credentials
 * work and the old ones no longer do, against the testing backend.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { NEW_PASSWORD, newAccount, seedAccount, submitSignIn } from "../support/flows";
import { mailSettings, waitForLink } from "../support/mail";

const mail = mailSettings();

test("a person recovers a forgotten password from the emailed link", async ({ page, request }) => {
  const account = newAccount("pw");
  await seedAccount(request, mail, account);

  await page.goto("/forgot-password");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("Request received")).toBeVisible();

  await page.goto(await waitForLink(mail, account.email, "/reset-password"));
  await expect(page).toHaveURL(/\/reset-password$/);
  await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Repeat new password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Password changed" })).toBeVisible();

  await page.getByRole("link", { name: "Go to sign in" }).click();
  await submitSignIn(page, { ...account, password: NEW_PASSWORD });
});

test("a person recovers a forgotten username from the emailed link", async ({ page, request }) => {
  const account = newAccount("un");
  await seedAccount(request, mail, account);
  const renamed = `${account.username}x`;

  await page.goto("/forgot-username");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("Request received")).toBeVisible();

  await page.goto(await waitForLink(mail, account.email, "/reset-username"));
  await expect(page).toHaveURL(/\/reset-username$/);
  await page.getByLabel("New username", { exact: true }).fill(renamed);
  await page.getByRole("button", { name: "Change username" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Username changed" })).toBeVisible();

  await page.getByRole("link", { name: "Go to sign in" }).click();
  await submitSignIn(page, { ...account, username: renamed });
});
