/**
 * Browser audit of every screen.
 *
 * Opens each screen of the built application at phone, tablet, and desktop widths and requires zero
 * axe violations, colour contrast included, and a layout with no horizontal scrolling or content
 * past the viewport edge. Interactive states are reached with clicks only, so no backend is needed.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { axeViolations, layoutProblems, VIEWPORTS } from "../support/checks";
import { FRAMES, mockSignedIn, mockSignedOut } from "../support/mock-backend";

const LINK = "#account=33333333-3333-4333-8333-333333333333&token=audit-token";

interface Screen {
  readonly name: string;
  readonly path: string;
  readonly signedIn: boolean;
  readonly prepare?: (page: Page) => Promise<void>;
}

async function submitEmpty(page: Page, label: string): Promise<void> {
  await page.getByRole("button", { name: label }).click();
  await expect(page.locator('[aria-invalid="true"]').first()).toBeVisible();
}

const SCREENS: readonly Screen[] = [
  { name: "sign-in", path: "/sign-in", signedIn: false },
  {
    name: "sign-in with validation errors",
    path: "/sign-in",
    signedIn: false,
    prepare: (page) => submitEmpty(page, "Sign in"),
  },
  { name: "register", path: "/register", signedIn: false },
  {
    name: "register with validation errors",
    path: "/register",
    signedIn: false,
    prepare: (page) => submitEmpty(page, "Create account"),
  },
  { name: "activate", path: `/activate${LINK}`, signedIn: false },
  { name: "activate without a link", path: "/activate", signedIn: false },
  { name: "resend activation", path: "/resend-activation", signedIn: false },
  { name: "forgot password", path: "/forgot-password", signedIn: false },
  { name: "forgot username", path: "/forgot-username", signedIn: false },
  { name: "reset password", path: `/reset-password${LINK}`, signedIn: false },
  {
    name: "reset password with validation errors",
    path: `/reset-password${LINK}`,
    signedIn: false,
    prepare: (page) => submitEmpty(page, "Change password"),
  },
  { name: "reset username", path: `/reset-username${LINK}`, signedIn: false },
  { name: "page not found", path: "/nowhere", signedIn: false },
  { name: "account", path: "/account", signedIn: true },
  {
    name: "account with the delete dialog open",
    path: "/account",
    signedIn: true,
    prepare: async (page) => {
      await page.getByRole("button", { name: "Delete account" }).click();
      await expect(page.getByRole("dialog", { name: "Delete your account?" })).toBeVisible();
    },
  },
  { name: "change password", path: "/account/password", signedIn: true },
  {
    name: "change password with validation errors",
    path: "/account/password",
    signedIn: true,
    prepare: (page) => submitEmpty(page, "Change password"),
  },
  { name: "change username", path: "/account/username", signedIn: true },
  { name: "notifications", path: "/notifications", signedIn: true },
  {
    name: "notifications with a populated list",
    path: "/notifications",
    signedIn: true,
    prepare: async (page) => {
      await expect(page.getByRole("button", { name: "Clear list" })).toBeVisible();
    },
  },
  {
    name: "notifications while offline",
    path: "/notifications",
    signedIn: true,
    prepare: async (page) => {
      await page.context().setOffline(true);
      await expect(page.getByText("Notifications paused")).toBeVisible();
    },
  },
];

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.name} (${String(viewport.width)} px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    for (const screen of SCREENS) {
      test(`${screen.name}: no axe violations and a layout that holds`, async ({ page }) => {
        if (screen.signedIn) {
          await mockSignedIn(page, screen.name.includes("populated") ? FRAMES : []);
        } else {
          await mockSignedOut(page);
        }

        await page.goto(screen.path);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await screen.prepare?.(page);

        expect(await axeViolations(page)).toEqual([]);
        expect(await layoutProblems(page)).toEqual([]);
      });
    }
  });
}
