/**
 * Page primitive.
 *
 * Renders the single level-one heading for a screen, sets the document title, and lays out the
 * content in a centered column.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";

import { usePageTitle } from "./usePageTitle";

/**
 * Page props.
 *
 * The page title, used as the heading and the document title, and the content.
 */
export interface PageProps {
  readonly title: string;
  readonly children: ReactNode;
}

/**
 * Render a page.
 *
 * Provides the one h1 of the screen and keeps the browser tab title in step with it.
 * Every screen renders through it, so no screen has more than one level-one heading.
 *
 * @param props - The page props.
 * @returns The page element.
 */
export function Page({ title, children }: PageProps): JSX.Element {
  usePageTitle(title);
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4 p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children}
    </div>
  );
}
