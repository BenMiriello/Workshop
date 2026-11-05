// What "show labels" numbers: every control on screen, in the shell and in
// the app frames that are shown, and every picture that clicks like one, in
// reading order, with its name and its box in the shell window's coordinates.

import { queryClient } from "../../../../lib/queryClient";
import { LABELS_RANGE, speechCommandsQuery, type SpeechCommands } from "../../../../lib/speech/commandsApi";
import { frameDocument } from "../../sameOriginFrames";
import { boxOf, documentShown, shown, visibleBox } from "../frames";
import { isPicture } from "./targetFacts";
import { nameFrom } from "./targetMatch";

export { findTarget, nameFrom, spokenNumber, type NameParts } from "./targetMatch";

export type TargetBox = { left: number; top: number; width: number; height: number };
export type Target = { n: number; name: string; el: Element; box: TargetBox };

/** The most labels shown at once: the operator's choice, saved with the
 *  spoken commands (Settings > Speech), up to LABELS_RANGE.max. */
export function targetsMax(): number {
  const c = queryClient.getQueryData<SpeechCommands>(speechCommandsQuery.queryKey);
  return Math.min(c?.labels.max ?? LABELS_DEFAULT, LABELS_RANGE.max);
}

const LABELS_DEFAULT = 100;

const ROLES = [
  "button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "checkbox",
  "radio", "switch", "option", "combobox", "textbox",
];

const ACTIONABLE = [
  "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary",
  ...ROLES.map((r) => `[role=${r}]`),
  "[contenteditable=true]", "[contenteditable='']", "[tabindex]:not([tabindex='-1'])",
].join(",");

// Controls whose content cannot hold another control, so one inside is part
// of it and is not numbered twice.
const ATOMIC = "a[href],button,summary,[role=button],[role=link]";

const SKIP = "[aria-hidden=true],[inert],.ws-dictpill,.ws-labels";

// Rows closer than this read as one line, so they go left to right.
const ROW = 8;

// A control opted out of dictation (data-dictation="off": a shortcut
// recorder, a phrase field) can still be clicked by its number.
function excluded(el: Element): boolean {
  if (el.closest(SKIP)) return true;
  if (el.matches(":disabled") || el.getAttribute("aria-disabled") === "true") return true;
  return !!el.parentElement?.closest(ATOMIC);
}

function documents(): Document[] {
  const out: Document[] = [];
  const walk = (doc: Document) => {
    out.push(doc);
    doc.querySelectorAll("iframe").forEach((f) => {
      if (!shown(f) || f.closest(SKIP)) return;
      const inner = frameDocument(f);
      if (inner && documentShown(inner)) walk(inner);
    });
  };
  walk(document);
  return out;
}

// Text a person reads in an element: icons, scripts and the options of a
// select are not part of its name.
const SILENT = new Set(["SCRIPT", "STYLE", "svg", "SELECT", "TEXTAREA", "TEMPLATE"]);

function textOf(node: Node): string {
  if (node.nodeType === 3) return node.textContent ?? "";
  if (node.nodeType !== 1) return "";
  const el = node as Element;
  if (SILENT.has(el.tagName) || el.getAttribute("aria-hidden") === "true") return "";
  let out = "";
  el.childNodes.forEach((c) => { out += textOf(c) + " "; });
  return out;
}

const PRESS_INPUTS = new Set(["button", "submit", "reset", "image", "checkbox", "radio", "file", "color"]);

function inputType(el: Element): string | null {
  return el.tagName === "INPUT" ? ((el as HTMLInputElement).type || "text").toLowerCase() : null;
}

/** Whether pressing it means typing into it rather than clicking it. */
function isTextField(el: Element): boolean {
  const type = inputType(el);
  if (type) return !PRESS_INPUTS.has(type);
  return el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable
    || el.getAttribute("role") === "textbox";
}

/** A practical accessible name: the label it is given, else what it says. */
export function accessibleName(el: Element): string {
  const doc = el.ownerDocument;
  const ids = el.getAttribute("aria-labelledby")?.split(/\s+/).filter(Boolean) ?? [];
  const labels = (el as HTMLInputElement).labels;
  const type = inputType(el);
  const img = type === "image" || el.tagName === "IMG" ? el : el.querySelector("img[alt]");
  return nameFrom({
    labelledBy: ids.map((id) => doc.getElementById(id)?.textContent ?? "").join(" "),
    ariaLabel: el.getAttribute("aria-label") ?? "",
    label: labels ? Array.from(labels, textOf).join(" ") : "",
    placeholder: el.getAttribute("placeholder") ?? "",
    value: type && PRESS_INPUTS.has(type) ? (el as HTMLInputElement).value : "",
    alt: img?.getAttribute("alt") ?? "",
    text: isTextField(el) || el.tagName === "SELECT" ? "" : textOf(el),
    title: el.getAttribute("title") ?? "",
  });
}

// A checkbox or radio drawn as a switch is a hidden input inside its label:
// the label is what is on screen.
export function shapeOf(el: Element): Element {
  return (el.matches("input[type=checkbox],input[type=radio]") && el.closest("label")) || el;
}

function onScreen(target: Element): TargetBox | null {
  const el = shapeOf(target);
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return null;
  const root = el.ownerDocument.documentElement;
  if (r.bottom <= 0 || r.right <= 0 || r.top >= root.clientHeight || r.left >= root.clientWidth) return null;
  const seen = visibleBox(el, boxOf(el));
  if (!seen) return null;
  return { left: seen.left, top: seen.top, width: seen.right - seen.left, height: seen.bottom - seen.top };
}

// A picture in a gallery is often clicked through a handler on a plain
// element, so it is not a control; its pointer cursor says it is one.
function clickablePicture(el: Element): boolean {
  if (el.closest(ACTIONABLE) || el.closest(SKIP) || !isPicture(el)) return false;
  return el.ownerDocument.defaultView?.getComputedStyle(el).cursor === "pointer";
}

/** Every control on screen now, numbered from 1 in reading order, the
 *  first targetsMax() from the top. */
export function collectTargets(): Target[] {
  const found: { el: Element; box: TargetBox }[] = [];
  const add = (el: Element) => {
    const box = onScreen(el);
    if (box) found.push({ el, box });
  };
  for (const doc of documents()) {
    doc.querySelectorAll(ACTIONABLE).forEach((el) => { if (!excluded(el)) add(el); });
    doc.querySelectorAll("img,video").forEach((el) => { if (clickablePicture(el)) add(el); });
  }
  found.sort((a, b) => Math.floor(a.box.top / ROW) - Math.floor(b.box.top / ROW) || a.box.left - b.box.left);
  return found.slice(0, targetsMax()).map((f, i) => ({ n: i + 1, name: accessibleName(f.el), ...f }));
}

/** Act on a target as a click would, or put the caret in it when it is a
 *  field. Returns what was done, for a notice. */
export function pressTarget(t: Target): string {
  const el = t.el as HTMLElement;
  const what = t.name || `number ${t.n}`;
  if (isTextField(el) || el.tagName === "SELECT") {
    el.focus();
    return `Focused ${what}`;
  }
  if (typeof el.focus === "function" && el.tabIndex >= 0) el.focus({ preventScroll: true });
  el.click();
  return `Clicked ${what}`;
}
