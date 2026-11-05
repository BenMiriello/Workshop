import type { SyntheticEvent } from "react";

/** Pressing an overlay control must not take focus from the field it serves or
 *  clear the selection it acts on; on a phone, losing focus also closes the
 *  keyboard the control floats on. */
export const keepFocus = (e: SyntheticEvent) => {
  if (e.cancelable) e.preventDefault();
};
