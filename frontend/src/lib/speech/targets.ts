// Which focused elements dictation may write into.
//
// A rule over a plain description, so it is testable without a DOM; the
// adapter that reads an element is beside it. The adapter compares TAG NAMES
// and never uses `instanceof`, because a field inside an app frame belongs to
// that frame's window, and `el instanceof HTMLInputElement` is false for it.

export type FieldDesc = {
  tag: string;
  type?: string;
  /** The `inputmode` attribute: number fields are often text inputs that
   *  only ask for a number keypad. */
  inputMode?: string;
  role?: string;
  editable: boolean;
  readOnly: boolean;
  disabled: boolean;
  /** The element or an ancestor carries `data-dictation="off"`. */
  optedOut: boolean;
};

/** "value" fields are written through their value setter; "editable" ones
 *  through the editing commands of their document. */
export type DictationKind = "value" | "editable";

// Words go into these. Everything else an <input> can be (password, number,
// date, color, range, file, checkbox, tel, ...) either cannot hold a phrase or
// should never receive one spoken aloud.
const TEXT_TYPES = new Set(["", "text", "search", "email", "url"]);
// A text input asking for one of these keypads is a number, a phone number or
// a field with its own picker, whatever its type says.
const NOT_WORDS_MODES = new Set(["numeric", "decimal", "tel", "none"]);

export function dictationKind(d: FieldDesc): DictationKind | null {
  if (d.optedOut || d.disabled || d.readOnly) return null;
  if (NOT_WORDS_MODES.has((d.inputMode ?? "").toLowerCase())) return null;
  if ((d.role ?? "").toLowerCase() === "spinbutton") return null;
  const tag = d.tag.toUpperCase();
  if (tag === "TEXTAREA") return "value";
  if (tag === "INPUT") return TEXT_TYPES.has((d.type ?? "").toLowerCase()) ? "value" : null;
  return d.editable ? "editable" : null;
}

export function describeField(el: Element): FieldDesc {
  const f = el as HTMLInputElement;
  return {
    tag: el.tagName,
    type: el.tagName === "INPUT" ? (el.getAttribute("type") ?? "") : undefined,
    inputMode: el.getAttribute("inputmode") ?? undefined,
    role: el.getAttribute("role") ?? undefined,
    editable: (el as HTMLElement).isContentEditable === true,
    readOnly: f.readOnly === true,
    disabled: f.disabled === true,
    optedOut: !!el.closest('[data-dictation="off"]'),
  };
}

/** The dictation kind of an element, or null when it is not a target. */
export function dictationKindOf(el: Element | null): DictationKind | null {
  return el ? dictationKind(describeField(el)) : null;
}
