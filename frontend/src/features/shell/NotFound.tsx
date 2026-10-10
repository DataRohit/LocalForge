/**
 * Not-found screen.
 *
 * Shown for any client route the route map does not define. It never renders for API paths,
 * which the edge routes to the backend.
 *
 * @packageDocumentation
 */

import type { JSX } from "react";

import { PATHS } from "../../session";
import { Page } from "../../ui/Page";
import { TextLink } from "../../ui/TextLink";

/**
 * Render the not-found screen.
 *
 * It offers a link to the start page, which routes by session state.
 * The edge routes API paths to the backend, so this screen never stands in for an API error.
 *
 * @returns The screen element.
 */
export function NotFound(): JSX.Element {
  return (
    <Page title="Page not found">
      <p>There is nothing at this address.</p>
      <p>
        <TextLink to={PATHS.home}>Go to the start page</TextLink>
      </p>
    </Page>
  );
}
