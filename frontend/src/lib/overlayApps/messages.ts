// The messages between the shell and an app opened over a page
// (docs/declaring-an-app-surface.md, "Opening over any page: overlay"). Same
// `{v: 1, type}` shape as every shell message, same-origin frames only.
//
//   app -> shell   workshop/v1/hello         ask what it was opened for
//   shell -> app   workshop/v1/context       {overlay: true, field: {name} | null}
//   app -> shell   workshop/v1/insertText    {text}: into the remembered field, then close
//   app -> shell   workshop/v1/closeOverlay  close it

export const HELLO_MSG = "workshop/v1/hello";
export const CONTEXT_MSG = "workshop/v1/context";
export const INSERT_MSG = "workshop/v1/insertText";
export const CLOSE_MSG = "workshop/v1/closeOverlay";
/** Shell to app: open your settings (an app whose overlay declares `settings`). */
export const OPEN_SETTINGS_MSG = "workshop/v1/openSettings";

/** Longest text an app may hand the shell to insert in one message. */
export const INSERT_MAX = 200_000;

export type OverlayRequest =
  | { kind: "hello" }
  | { kind: "insert"; text: string }
  | { kind: "close" };

/** What an app's message asks of the shell, or null when it is not one of
 *  the overlay messages (or carries nothing to insert). */
export function parseOverlayMessage(data: unknown): OverlayRequest | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.v !== 1) return null;
  if (d.type === HELLO_MSG) return { kind: "hello" };
  if (d.type === CLOSE_MSG) return { kind: "close" };
  if (d.type === INSERT_MSG && typeof d.text === "string" && d.text && d.text.length <= INSERT_MAX) {
    return { kind: "insert", text: d.text };
  }
  return null;
}

export function contextMessage(fieldName: string | null) {
  return { v: 1, type: CONTEXT_MSG, overlay: true, field: fieldName === null ? null : { name: fieldName } };
}
