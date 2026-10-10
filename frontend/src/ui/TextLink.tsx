/**
 * Text link primitive.
 *
 * A router link styled as underlined text with a visible focus style. All in-app navigation
 * goes through it, so links stay semantic anchors.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";
import { Link } from "react-router";

/**
 * Text link props.
 *
 * The client route to open and the link text.
 */
export interface TextLinkProps {
  readonly to: string;
  readonly children: ReactNode;
}

/**
 * Render a text link.
 *
 * It renders a real anchor, so open in new tab and copy link keep working.
 * Buttons, not links, perform actions; links only navigate.
 *
 * @param props - The link props.
 * @returns The anchor element.
 */
export function TextLink({ to, children }: TextLinkProps): JSX.Element {
  return (
    <Link to={to} className="font-medium text-brand underline underline-offset-2">
      {children}
    </Link>
  );
}
