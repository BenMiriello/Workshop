// Walking a real document into outline lines (rules and format in outline.ts).
// Reaches into same-origin frames, skips what is not shown, and skips the
// shell's own layers (the drawer itself, overlays drawn for speech), which are
// not part of the page.

import {
  capLines, extrasOf, formatLine, isInteractive, matchLines, nameOf, roleOf, signature,
  type Line, type Node, type OutlineState,
} from "./outline";

const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "HEAD", "META", "LINK"]);
const SKIP_SELECTOR = ".ws-beside, .ws-speech-layer, [data-beside-skip]";
const TEXT_SHORT = 160;
const MAX_NODES = 6000;

function attrsOf(el: Element): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of Array.from(el.attributes)) out[a.name] = a.value;
  if (el.tagName === "A") out.href = (el as HTMLAnchorElement).href || out.href;
  if ((el as HTMLInputElement).checked) out.checked = "";
  return out;
}

function hidden(el: Element): boolean {
  if (el.getAttribute("aria-hidden") === "true" || el.hasAttribute("hidden")) return true;
  const view = el.ownerDocument.defaultView;
  if (!view) return true;
  const style = view.getComputedStyle(el);
  return style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse";
}

function labelFor(el: Element): string {
  const doc = el.ownerDocument;
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    return by.split(/\s+/).map((id) => doc.getElementById(id)?.textContent || "").join(" ");
  }
  const id = el.getAttribute("id");
  if (id) {
    const label = doc.querySelector(`label[for="${CSS.escape(id)}"]`);
    if (label) return label.textContent || "";
  }
  return el.closest("label")?.textContent || "";
}

function valueOf(el: Element): string {
  if (el instanceof el.ownerDocument.defaultView!.HTMLSelectElement) {
    const s = el as HTMLSelectElement;
    return s.selectedOptions[0]?.textContent || "";
  }
  if ("value" in el && typeof (el as HTMLInputElement).value === "string") return (el as HTMLInputElement).value;
  if ((el as HTMLElement).isContentEditable) return (el as HTMLElement).innerText || "";
  return "";
}

function directText(el: Element): string {
  let out = "";
  for (const c of Array.from(el.childNodes)) if (c.nodeType === 3) out += c.textContent || "";
  return out.replace(/\s+/g, " ").trim();
}

function refFor(state: OutlineState, el: Element): string {
  let ref = state.ids.get(el);
  if (!ref) {
    state.seq += 1;
    ref = `e${state.seq}`;
    state.ids.set(el, ref);
  }
  state.refs.set(ref, el);
  return ref;
}

/** Outline lines for everything under `root`. */
export function collect(root: Element, state: OutlineState, all: boolean): Line[] {
  const lines: Line[] = [];
  const base = root.ownerDocument.location?.origin || "";
  let count = 0;
  const walk = (el: Element, depth: number, inInteractive: boolean) => {
    if (count++ > MAX_NODES || SKIP_TAGS.has(el.tagName.toUpperCase())) return;
    if (el.matches(SKIP_SELECTOR) || hidden(el)) return;
    if (el.tagName === "IFRAME") {
      try {
        const body = (el as HTMLIFrameElement).contentDocument?.body;
        if (body) walk(body, depth, inInteractive);
      } catch { /* another origin: nothing to read */ }
      return;
    }
    const attrs = attrsOf(el);
    const node: Node = { tag: el.tagName.toLowerCase(), attrs, text: "" };
    const role = roleOf(node);
    let next = depth;
    if (role) {
      const interactive = isInteractive(role);
      const textual = interactive || role === "heading" || role === "img" || role === "dialog";
      node.text = textual ? (el.textContent || "") : "";
      const level = role === "heading" ? `[${el.tagName.slice(1)}]` : "";
      lines.push({
        depth,
        role: role + level,
        name: nameOf(node, interactive ? labelFor(el) : ""),
        ref: interactive ? refFor(state, el) : undefined,
        extra: extrasOf(node, role, interactive ? valueOf(el) : "", base),
      });
      next = depth + 1;
      if (textual) inInteractive = true;
    } else if (!inInteractive) {
      const text = directText(el);
      if (text && (all || text.length > 1)) {
        lines.push({ depth, role: "text", name: all ? text : text.slice(0, TEXT_SHORT), extra: "" });
      }
    }
    for (const child of Array.from(el.children)) walk(child, next, inInteractive);
  };
  walk(root, 0, false);
  return lines;
}

/** `read_page`: the outline of `root`, or of the element `ref` names. */
export function readPage(root: Element, state: OutlineState, args: { ref?: string; all?: boolean }): string {
  const start = args.ref ? state.refs.get(args.ref) : root;
  if (args.ref && (!start || !start.isConnected)) {
    return `There is no element ${args.ref} on the page now. Read the page again.`;
  }
  const prev = state.seen;
  if (!args.ref) state.refs.clear();
  const lines = collect(start!, state, !!args.all);
  const sigs = lines.map(signature);
  if (!args.ref) state.seen = new Set(sigs);
  const text = lines.map((l, i) => formatLine(l, !!prev && !prev.has(sigs[i])));
  return capLines(text);
}

/** `find`: the outline lines that match, with their refs. */
export function findOnPage(root: Element, state: OutlineState, query: string): string {
  const lines = collect(root, state, true).map((l) => formatLine({ ...l, depth: 0 }, false));
  const hits = matchLines(lines, query);
  if (!hits.length) return `Nothing on the page matches "${query}".`;
  return capLines(hits.slice(0, 80), 6000);
}

/** What appeared since the last full reading, for an action's answer. */
export function changesSince(root: Element, state: OutlineState): string {
  const prev = state.seen;
  const lines = collect(root, state, false);
  const sigs = lines.map(signature);
  state.seen = new Set(sigs);
  if (!prev) return "";
  const fresh = lines.filter((_, i) => !prev.has(sigs[i])).map((l) => formatLine({ ...l, depth: 0 }, true));
  if (!fresh.length) return "Nothing new appeared on the page.";
  return `New on the page:\n${capLines(fresh.slice(0, 40), 3000)}`;
}
