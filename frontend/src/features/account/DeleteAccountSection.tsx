/**
 * Delete-account section.
 *
 * The destructive action kept apart from the editing forms: a button that opens a confirmation
 * dialog. Nothing is deleted until the person confirms inside the dialog.
 *
 * @packageDocumentation
 */

import { useState } from "react";
import type { JSX } from "react";

import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { DeleteAccountForm } from "./DeleteAccountForm";

/**
 * Render the delete-account section.
 *
 * Owns the open state of the dialog. Closing it, by cancelling or pressing Escape, returns focus to
 * the button that opened it.
 *
 * @returns The section element.
 */
export function DeleteAccountSection(): JSX.Element {
  const [open, setOpen] = useState(false);
  const close = (): void => {
    setOpen(false);
  };
  return (
    <Card>
      <h2 className="text-lg font-semibold">Delete account</h2>
      <p className="text-sm text-muted">
        Permanently remove your account. You will be asked to confirm with your password.
      </p>
      <div>
        <Button
          variant="danger"
          onClick={() => {
            setOpen(true);
          }}
        >
          Delete account
        </Button>
      </div>
      <Dialog title="Delete your account?" open={open} onClose={close}>
        <DeleteAccountForm onCancel={close} />
      </Dialog>
    </Card>
  );
}
