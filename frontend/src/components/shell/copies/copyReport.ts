// What the shell reports for one copy, and how it reads the text a copy took.
// Pure, so it is tested without a DOM; CopyRelay.tsx does the listening.

/** The backend refuses anything longer (services/app_events.MAX_TEXT). */
export const MAX_COPIED_TEXT = 100_000;
/** services/app_events.Source.app's limit. */
export const MAX_APP_NAME = 200;

/** `app` names the app on the computer a copy was made in, for one the Mac
 *  app saw outside Workshop; it is absent for a copy made in Workshop. */
export type CopiedReport = { text: string; source: { path: string; app?: string } };

/** The body posted for a copy of `text` made on the page at `path` (or, with
 *  `app`, in that app while the page was open), or null when nothing worth
 *  telling an app was copied. */
export function copiedReport(text: string, path: string, app?: string): CopiedReport | null {
  if (!text.trim()) return null;
  const source = app ? { path, app: app.slice(0, MAX_APP_NAME) } : { path };
  return { text: text.slice(0, MAX_COPIED_TEXT), source };
}

/** The selected part of a text field's value. A selection inside an input or
 *  a textarea is not part of the document selection in every engine, so a
 *  copy from one is read here instead. A field type with no selection API
 *  reports null offsets and gives nothing. */
export function fieldSelection(value: string, start: number | null, end: number | null): string {
  if (start == null || end == null || end <= start) return "";
  return value.slice(start, end);
}
