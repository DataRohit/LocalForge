/**
 * Browser checks for the audit.
 *
 * Runs axe-core with every rule, including colour contrast, in the real page, and measures the
 * layout for horizontal overflow and content that is cut off at the viewport edges.
 *
 * @packageDocumentation
 */

import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

/**
 * A viewport the audit covers.
 *
 * A name for the test title and the size in CSS pixels.
 */
export interface Viewport {
  readonly name: string;
  readonly width: number;
  readonly height: number;
}

/**
 * The three widths every screen is audited at.
 *
 * Phone, tablet, and desktop.
 */
export const VIEWPORTS: readonly Viewport[] = [
  { name: "phone", width: 360, height: 740 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 800 },
];

/**
 * Find accessibility violations in the page.
 *
 * Runs all axe rules, so colour contrast is included, and formats each violation with the
 * selectors it affects.
 *
 * @param page - The page to check.
 * @returns One line per violation, empty when the page passes.
 */
export async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} -> ${violation.nodes
        .map((node) => node.target.join(" "))
        .join(", ")}`,
  );
}

/**
 * Find layout problems at the current viewport.
 *
 * Reports a page that scrolls horizontally and any visible element whose box extends past either
 * edge of the viewport, which is how clipped content shows up.
 *
 * @param page - The page to measure.
 * @returns One line per problem, empty when the layout holds.
 */
export async function layoutProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const problems: string[] = [];
    if (document.documentElement.scrollWidth > width) {
      problems.push(
        `the page scrolls horizontally (${String(document.documentElement.scrollWidth)} > ${String(width)})`,
      );
    }
    for (const element of document.body.querySelectorAll("*")) {
      const box = element.getBoundingClientRect();
      if (box.width > 1 && box.height > 1 && (box.right > width + 1 || box.left < -1)) {
        const text = element.textContent.trim().slice(0, 30);
        problems.push(
          `${element.tagName.toLowerCase()} "${text}" spans ${String(Math.round(box.left))} to ${String(Math.round(box.right))}`,
        );
      }
    }
    return problems;
  });
}
