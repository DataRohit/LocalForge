/**
 * Dialog primitive.
 *
 * A modal dialog built on the native dialog element, which traps focus, blocks the page behind it,
 * and closes on Escape. It returns focus to the control that opened it when it closes.
 *
 * @packageDocumentation
 */

import { useEffect, useId, useState } from "react";
import type { JSX, ReactNode } from "react";

/**
 * Dialog props.
 *
 * The visible title, whether the dialog is open, the handler for a request to close it, and the
 * content, which is rendered only while open so its state resets each time.
 */
export interface DialogProps {
  readonly title: string;
  readonly open: boolean;
  readonly onClose: () => void;
  readonly children: ReactNode;
}

/**
 * Render a modal dialog.
 *
 * Opens and closes the native dialog from the open prop, names it with its heading, and turns
 * Escape into a call to onClose so the owner stays in charge of the open state.
 *
 * @param props - The dialog props.
 * @returns The dialog element.
 */
export function Dialog({ title, open, onClose, children }: DialogProps): JSX.Element {
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    if (dialog === null || !open) {
      return undefined;
    }
    const opener = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (opener instanceof HTMLElement) {
        opener.focus();
      }
    };
  }, [dialog, open]);

  return (
    <dialog
      ref={setDialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-auto w-full max-w-md rounded-card border border-line bg-surface p-6 text-ink backdrop:bg-ink/50"
    >
      <div className="flex flex-col gap-4">
        <h2 id={titleId} className="text-xl font-semibold">
          {title}
        </h2>
        {open ? children : null}
      </div>
    </dialog>
  );
}
