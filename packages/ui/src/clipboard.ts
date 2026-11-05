// Copying text from code. A copy made this way fires no DOM `copy` event, so
// anything watching for copies (the shell tells subscribed apps about each one)
// would never see it. `copyText` writes the clipboard and then announces the
// copy on its window as `COPIED_EVENT`, with `{ text }` as the detail.

export const COPIED_EVENT = "workshop:copied";

export type CopiedDetail = { text: string };

/** Write `text` to the clipboard. Resolves true once it is there, false when
 *  the browser refused or has no clipboard; only a copy that landed is
 *  announced. */
export async function copyText(text: string): Promise<boolean> {
  if (!navigator.clipboard) return false;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    return false;
  }
  window.dispatchEvent(new CustomEvent<CopiedDetail>(COPIED_EVENT, { detail: { text } }));
  return true;
}
