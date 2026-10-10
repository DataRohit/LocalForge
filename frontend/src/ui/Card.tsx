/**
 * Card primitive.
 *
 * Groups related content on a bordered surface. It is the only container the screens use for
 * a form or a block of account details.
 *
 * @packageDocumentation
 */

import type { JSX, ReactNode } from "react";

/**
 * Card props.
 *
 * The content to group.
 */
export interface CardProps {
  readonly children: ReactNode;
}

/**
 * Render a card.
 *
 * It adds no behavior; it only provides the surface and spacing.
 * Related fields and actions belong together in one card, not in nested cards.
 *
 * @param props - The card props.
 * @returns The card element.
 */
export function Card({ children }: CardProps): JSX.Element {
  return (
    <div className="flex flex-col gap-4 rounded-card border border-line bg-surface p-4 sm:p-6">
      {children}
    </div>
  );
}
