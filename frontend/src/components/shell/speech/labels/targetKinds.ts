// What kind of control a target is and how it is described to the meaning
// matcher, from facts the DOM walk reads (targetFacts.ts). Pure, so it can be
// run in tests.

export type TargetKind =
  | "button" | "link" | "tab" | "field" | "select" | "checkbox" | "radio" | "switch"
  | "option" | "menu item" | "image";

export type KindFacts = { tag: string; role: string; type: string; editable: boolean };

const ROLE_KINDS: Record<string, TargetKind> = {
  button: "button", link: "link", tab: "tab", textbox: "field", searchbox: "field",
  combobox: "select", listbox: "select", checkbox: "checkbox", menuitemcheckbox: "checkbox",
  radio: "radio", menuitemradio: "radio", switch: "switch", option: "option", menuitem: "menu item",
  img: "image",
};

const INPUT_KINDS: Record<string, TargetKind> = {
  button: "button", submit: "button", reset: "button", image: "button", file: "button",
  color: "button", checkbox: "checkbox", radio: "radio", range: "field",
};

export function kindOf(f: KindFacts): TargetKind {
  const byRole = ROLE_KINDS[f.role];
  if (byRole) return byRole;
  switch (f.tag) {
    case "A": return "link";
    case "SELECT": return "select";
    case "TEXTAREA": return "field";
    case "IMG": case "VIDEO": return "image";
    case "INPUT": return INPUT_KINDS[f.type] ?? "field";
    default: return f.editable ? "field" : "button";
  }
}

// What lucide's shape names stand for when said, so "the gear" finds the
// settings icon and "the little x" the close one. Generic to the icon set,
// never to a page. Measured with and without: 79 and 76 of 92 requests found
// their control first.
const ICON_MEANS: Record<string, string> = {
  settings: "gear", cog: "settings", x: "close", "circle x": "close",
  plus: "add new", trash: "delete bin", pencil: "edit", pen: "edit", "square pen": "edit",
  "pen line": "edit", ellipsis: "more menu dots", "ellipsis vertical": "more menu dots",
  "more horizontal": "more menu dots", "more vertical": "more menu dots", menu: "hamburger",
  search: "magnifying glass", "arrow left": "back", "chevron left": "back",
  "chevron right": "next forward", "chevron down": "expand open", "chevron up": "collapse",
  mic: "microphone", copy: "duplicate", "refresh cw": "reload", "rotate cw": "reload",
  house: "home", "log out": "sign out", "circle help": "help question", info: "information",
  "sliders horizontal": "filters", funnel: "filter", "list filter": "filter funnel", star: "favorite", heart: "like",
  bell: "notifications", "panel left": "sidebar", maximize: "fullscreen", square: "stop",
  check: "done confirm", eye: "show", "eye off": "hide", user: "profile account",
  image: "picture", send: "paper plane", paperclip: "attach", "external link": "open new",
};

/** The name of a lucide icon from its class: "lucide lucide-trash-2" -> "trash". */
export function iconName(className: string): string {
  const c = className.split(/\s+/).find((w) => w.startsWith("lucide-") && w !== "lucide-icon");
  return c ? c.slice("lucide-".length).replace(/-?\d+$/, "").replace(/-/g, " ") : "";
}

/** An icon's name with what its shape stands for, when that is not its name. */
export function iconMeaning(name: string): string {
  const means = ICON_MEANS[name];
  return means ? `${name} ${means}` : name;
}

export type DescribeFacts = {
  name: string; kind: TargetKind; image: boolean; icon: string; title: string; row: string; context: string;
};

const KIND_WORDS: Record<TargetKind, string> = {
  button: "button", link: "link", tab: "tab", field: "text field", select: "dropdown",
  checkbox: "checkbox", radio: "radio button", switch: "switch", option: "option",
  "menu item": "menu item", image: "image",
};

/** One line saying what a control is, for matching by meaning:
 *  "Sign out, button, log out sign out icon, Mac seen 4d ago, in Sessions". */
export function describeTarget(f: DescribeFacts): string {
  const kind = f.image && f.kind !== "image" ? `image ${KIND_WORDS[f.kind]}` : KIND_WORDS[f.kind];
  const parts = [f.name || f.title, kind];
  if (f.icon) parts.push(`${iconMeaning(f.icon)} icon`);
  if (f.title && f.title !== f.name) parts.push(f.title);
  if (f.row) parts.push(f.row);
  if (f.context && f.context !== f.name) parts.push(`in ${f.context}`);
  return parts.filter(Boolean).join(", ");
}
