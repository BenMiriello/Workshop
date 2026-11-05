// The platform's reading of a page for the chat beside it: `read_page` and
// `find` from the contract (docs/chat-beside-a-page.md), over plain DOM, for any
// page or same-origin app frame that does not answer them itself.
//
// One line per element worth naming, indented by the named elements around it:
//   heading[2] "Library"
//   button "Save" [ref=e12] [disabled]
//   textbox "Title" [ref=e5] = "draft"
//   link "Docs" [ref=e3] -> /docs
//   text "Three items selected"
// Interactive elements get a ref. A ref stays with its element for as long as
// the element exists, so a ref read earlier still works if the element is still
// there. Lines that were not in the previous reading start with `*`.

export type Node = { tag: string; attrs: Record<string, string>; text: string };

export type Line = { depth: number; role: string; name: string; ref?: string; extra: string };

export type OutlineState = {
  refs: Map<string, Element>;
  ids: WeakMap<Element, string>;
  seq: number;
  seen: Set<string> | null;
};

export const READ_CAP = 12000;

export function newOutlineState(): OutlineState {
  return { refs: new Map(), ids: new WeakMap(), seq: 0, seen: null };
}

const INPUT_ROLES: Record<string, string> = {
  button: "button", submit: "button", reset: "button", image: "button",
  checkbox: "checkbox", radio: "radio", range: "slider", search: "searchbox",
  file: "button", color: "button",
};

const LANDMARKS: Record<string, string> = {
  nav: "navigation", main: "main", header: "banner", footer: "contentinfo",
  aside: "complementary", form: "form", dialog: "dialog", ul: "list", ol: "list",
  table: "table",
};

const INTERACTIVE = new Set([
  "link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "slider",
  "switch", "tab", "menuitem", "option", "listbox", "treeitem", "spinbutton",
]);

/** The role an element plays, or "" for one not worth a line of its own. */
export function roleOf(n: Node): string {
  const explicit = (n.attrs.role || "").split(/\s+/)[0];
  if (explicit && explicit !== "presentation" && explicit !== "none") return explicit;
  const t = n.tag;
  if (t === "a") return n.attrs.href !== undefined ? "link" : "";
  if (t === "button" || t === "summary") return "button";
  if (t === "input") {
    if (n.attrs.type === "hidden") return "";
    return INPUT_ROLES[n.attrs.type || "text"] || "textbox";
  }
  if (t === "textarea") return "textbox";
  if (t === "select") return n.attrs.multiple !== undefined ? "listbox" : "combobox";
  if (/^h[1-6]$/.test(t)) return "heading";
  if (t === "img") return n.attrs.alt ? "img" : "";
  if (n.attrs.contenteditable === "true" || n.attrs.contenteditable === "") return "textbox";
  if (LANDMARKS[t]) return LANDMARKS[t];
  if (n.attrs.tabindex !== undefined && Number(n.attrs.tabindex) >= 0) return "button";
  return "";
}

export function isInteractive(role: string): boolean {
  return INTERACTIVE.has(role);
}

function flat(text: string, cap: number): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > cap ? `${t.slice(0, cap - 1)}…` : t;
}

/** The accessible name, from the attributes that carry one before the text. */
export function nameOf(n: Node, labelText = ""): string {
  const a = n.attrs;
  return flat(a["aria-label"] || labelText || a.alt || a.title || a.placeholder
    || (n.tag === "input" && ["button", "submit", "reset"].includes(a.type || "") ? a.value || "" : "")
    || n.text, 120);
}

export function formatLine(l: Line, fresh: boolean): string {
  const pad = "  ".repeat(Math.min(l.depth, 12));
  const name = l.name ? ` "${l.name.replace(/"/g, "'")}"` : "";
  const ref = l.ref ? ` [ref=${l.ref}]` : "";
  return `${pad}${fresh ? "*" : ""}${l.role}${name}${ref}${l.extra}`;
}

export function signature(l: Line): string {
  return `${l.role}|${l.name}|${l.extra}`;
}

/** The lines joined, cut at `cap` characters with a note saying what was left. */
export function capLines(lines: string[], cap = READ_CAP): string {
  let used = 0;
  let i = 0;
  for (; i < lines.length; i++) {
    if (used + lines[i].length + 1 > cap) break;
    used += lines[i].length + 1;
  }
  const out = lines.slice(0, i).join("\n");
  const left = lines.length - i;
  return left > 0
    ? `${out}\n(${left} more lines not shown: read one part with ref, or use page_find.)`
    : out || "(the page shows nothing to read)";
}

/** Extra facts for a line: value, state, link target. Pure over the node. */
export function extrasOf(n: Node, role: string, value: string, base: string): string {
  const a = n.attrs;
  let out = "";
  if ((role === "textbox" || role === "searchbox" || role === "combobox") && value) {
    out += ` = "${flat(a.type === "password" ? "••••" : value, 80)}"`;
  }
  if (a.checked !== undefined || a["aria-checked"] === "true") out += " [checked]";
  if (a["aria-selected"] === "true") out += " [selected]";
  if (a["aria-expanded"] === "true") out += " [expanded]";
  if (a.disabled !== undefined || a["aria-disabled"] === "true") out += " [disabled]";
  if (role === "link" && a.href && !a.href.startsWith("javascript:")) {
    out += ` -> ${a.href.startsWith(base) ? a.href.slice(base.length) || "/" : a.href}`;
  }
  return out;
}

/** Outline lines matching `query`: every word must appear in the line. */
export function matchLines(lines: string[], query: string): string[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return lines.filter((l) => words.every((w) => l.toLowerCase().includes(w)));
}
