/**
 * Dialog shim for tests.
 *
 * jsdom does not implement showModal or close on the dialog element. This shim reproduces what the
 * tests rely on: the open attribute, the close event, and focus moving to the first control.
 *
 * @packageDocumentation
 */

const FOCUSABLE = "input, button, select, textarea, a[href], [tabindex]";

/**
 * Open a dialog the way a browser does.
 *
 * Sets the open attribute and focuses the first focusable control inside, which is what the native
 * modal does. Focus trapping and the backdrop are covered by the browser suites instead.
 *
 * @param this - The dialog element.
 */
function showModal(this: HTMLDialogElement): void {
  this.setAttribute("open", "");
  this.querySelector<HTMLElement>(FOCUSABLE)?.focus();
}

/**
 * Close a dialog the way a browser does.
 *
 * Removes the open attribute and fires the close event.
 *
 * @param this - The dialog element.
 */
function close(this: HTMLDialogElement): void {
  this.removeAttribute("open");
  this.dispatchEvent(new Event("close"));
}

HTMLDialogElement.prototype.showModal = showModal;
HTMLDialogElement.prototype.close = close;
