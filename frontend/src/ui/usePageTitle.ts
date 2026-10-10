/**
 * Document title hook.
 *
 * Sets the browser tab title for the lifetime of a screen and restores the previous title
 * afterwards. The title is the only document property a screen sets.
 *
 * @packageDocumentation
 */

import { useEffect } from "react";

/**
 * Set the document title.
 *
 * Prefixes the product name and restores the earlier title when the screen unmounts.
 * A screen that unmounts hands the title back, so the next screen starts from a known state.
 *
 * @param title - The screen title.
 */
export function usePageTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = `${title} · LocalForge`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
