/**
 * Accessibility checks for tests.
 *
 * Runs axe-core on the rendered document and reports each violation as one line. Colour contrast
 * needs real layout and painting, so jsdom skips it and the browser audit covers it instead.
 *
 * @packageDocumentation
 */

import axe from "axe-core";

/**
 * Find accessibility violations in the document.
 *
 * Runs every axe rule except colour contrast over the whole body, and formats each violation with
 * its rule, its help text, and the selectors it affects, so a failure names what to fix.
 *
 * @param root - The element to check, defaulting to the whole body.
 * @returns One line per violation, empty when the document passes.
 */
export async function axeViolations(root: Element = document.body): Promise<string[]> {
  const results = await axe.run(root, {
    rules: { "color-contrast": { enabled: false } },
    resultTypes: ["violations"],
  });
  return results.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} -> ${violation.nodes
        .map((node) => node.target.join(" "))
        .join(", ")}`,
  );
}
