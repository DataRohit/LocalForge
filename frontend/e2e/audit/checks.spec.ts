/**
 * Negative control for the browser audit.
 *
 * Feeds the audit checks a page with a known contrast failure and a known overflow, and requires
 * them to be reported. Without this a clean audit could simply mean the checks look at nothing.
 *
 * @packageDocumentation
 */

import { expect, test } from "@playwright/test";

import { axeViolations, layoutProblems } from "../support/checks";

test.use({ viewport: { width: 360, height: 740 } });

test.describe("the audit checks", () => {
  test("report a colour contrast failure", async ({ page }) => {
    await page.setContent(
      '<!doctype html><html lang="en"><head><title>x</title></head><body><main>' +
        '<h1>Heading</h1><p style="color:#bbbbbb;background:#ffffff">Pale text</p></main></body></html>',
    );

    const found = await axeViolations(page);

    expect(found.some((line) => line.startsWith("color-contrast"))).toBe(true);
  });

  test("report a page that scrolls sideways and content past the edge", async ({ page }) => {
    await page.setContent(
      '<!doctype html><html lang="en"><head><title>x</title></head><body><main>' +
        '<h1>Heading</h1><div style="width:1200px">Too wide for a phone</div></main></body></html>',
    );

    const found = await layoutProblems(page);

    expect(found.some((line) => line.includes("scrolls horizontally"))).toBe(true);
    expect(found.some((line) => line.includes("Too wide"))).toBe(true);
  });

  test("accept a page that is fine", async ({ page }) => {
    await page.setContent(
      '<!doctype html><html lang="en"><head><title>x</title></head><body><main>' +
        '<h1>Heading</h1><p style="color:#111111;background:#ffffff">Readable text</p></main></body></html>',
    );

    expect(await axeViolations(page)).toEqual([]);
    expect(await layoutProblems(page)).toEqual([]);
  });
});
