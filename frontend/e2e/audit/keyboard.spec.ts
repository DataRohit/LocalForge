/**
 * Keyboard and focus audit in a real browser.
 *
 * Checks what jsdom cannot: that every control shows a visible focus indicator, that focus moves to
 * the new content after navigation, and that the native dialog traps focus, closes on Escape, and
 * hands focus back to the control that opened it.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockSignedIn, mockSignedOut } from "../support/mock-backend";

test.use({ viewport: { width: 1280, height: 800 } });

interface FocusStop {
  readonly name: string;
  readonly outlineWidth: number;
  readonly outlineStyle: string;
}

async function focusStop(page: Page): Promise<FocusStop> {
  return page.evaluate(() => {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement)) {
      return { name: "", outlineWidth: 0, outlineStyle: "none" };
    }
    const style = getComputedStyle(element);
    return {
      name: element.getAttribute("aria-label") ?? element.textContent.trim().slice(0, 40),
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineStyle: style.outlineStyle,
    };
  });
}

test.describe("keyboard use", () => {
  test("every stop of the sign-in page shows a visible focus indicator", async ({ page }) => {
    await mockSignedOut(page);
    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();

    const stops: FocusStop[] = [];
    for (let step = 0; step < 11; step += 1) {
      await page.keyboard.press("Tab");
      stops.push(await focusStop(page));
    }

    expect(stops.map((stop) => stop.name)).toEqual([
      "Skip to main content",
      "LocalForge",
      "Sign in",
      "Register",
      "",
      "",
      "Sign in",
      "Forgot your password?",
      "Forgot your username?",
      "Create an account",
      "Resend the activation email",
    ]);
    for (const stop of stops) {
      expect(stop.outlineStyle).not.toBe("none");
      expect(stop.outlineWidth).toBeGreaterThanOrEqual(2);
    }
  });

  test("focus moves to the main region after navigating", async ({ page }) => {
    await mockSignedOut(page);
    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Register" })
      .click();

    await expect(page.getByRole("heading", { level: 1, name: "Create an account" })).toBeVisible();
    await expect(page.locator("#main")).toBeFocused();
  });

  test("the delete dialog keeps focus off the page behind it, closes on Escape, and returns focus", async ({
    page,
  }) => {
    await mockSignedIn(page);
    await page.goto("/account");
    const opener = page.getByRole("button", { name: "Delete account" });
    await opener.focus();

    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog", { name: "Delete your account?" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Current password")).toBeFocused();
    for (let step = 0; step < 8; step += 1) {
      await page.keyboard.press("Tab");
      const strayed = await page.evaluate(() => {
        const active = document.activeElement;
        return active !== document.body && active?.closest("dialog") === null;
      });
      expect(strayed).toBe(false);
    }

    await page.keyboard.press("Escape");

    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
