// Spoken moves around the page that are not clicks: scroll to either end, close
// whatever is open on top, step through tabs, and choose a field to write in.
// Like scrolling and clicking these are not in the action history: each is
// taken back by its opposite.

import { documentShown, shown } from "./frames";
import { notCommand, short, type NoticeHost } from "./actionRun";
import type { CommandHost } from "./dictation";
import { destinationFor } from "./destination";
import { findTarget, type Target } from "./labels/pageTargets";
import { pageLabels } from "./labels/pageLabels";
import { numbered, numberGroups } from "./numbered";
import { scrollerAtCentre } from "./pageActions";
import type { Run } from "./voiceActionTypes";

export function scrollEdge(host: NoticeHost, bottom: boolean): void {
  const el = scrollerAtCentre();
  const end = bottom ? el.scrollHeight - el.clientHeight : 0;
  if (Math.abs(el.scrollTop - end) <= 1) { host.notice({ text: bottom ? "Already at the bottom" : "Already at the top" }); return; }
  el.scrollTo({ top: end, behavior: "smooth" });
}

const OVER = "[role=dialog], [role=alertdialog], [role=menu], [role=listbox], dialog[open]";

/** The documents on screen, the shell's first, then each shown app frame's. */
function documents(): Document[] {
  const out = [document];
  for (const f of document.querySelectorAll("iframe")) {
    try {
      const d = f.contentDocument;
      if (d && shown(f) && documentShown(d)) out.push(d);
    } catch { /* another origin */ }
  }
  return out;
}

function visible(el: Element): boolean {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && el.ownerDocument.defaultView?.getComputedStyle(el).visibility !== "hidden";
}

/** "Close that": the list of numbers showing, else the topmost dialog or
 *  menu open on screen, closed as Escape would close it. */
export function closeTop(host: NoticeHost): void {
  if (numbered.get()) { numbered.close(); return; }
  const open = documents().flatMap((d) => [...d.querySelectorAll(OVER)]).filter(visible);
  const top = open[open.length - 1];
  if (!top) { host.notice({ text: "Nothing open to close" }); return; }
  const win = top.ownerDocument.defaultView ?? window;
  top.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
}

function tabsOnScreen(): HTMLElement[][] {
  return documents().flatMap((d) => [...d.querySelectorAll("[role=tablist]")])
    .filter(visible)
    .map((list) => [...list.querySelectorAll<HTMLElement>("[role=tab]")].filter((t) => visible(t) && t.closest("[role=tablist]") === list))
    .filter((tabs) => tabs.length > 1);
}

/** "Next tab": the tab after the selected one, in the first row of tabs on
 *  screen; wraps around. */
export function stepTab(host: NoticeHost, by: 1 | -1): void {
  const tabs = tabsOnScreen()[0];
  if (!tabs) { host.notice({ text: "No tabs here" }); return; }
  const at = tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
  const next = tabs[(Math.max(at, 0) + by + tabs.length) % tabs.length];
  next.click();
  next.focus({ preventScroll: true });
}

const FIELD = "input, textarea, [contenteditable=''], [contenteditable=true], [role=textbox], [role=combobox], [role=searchbox]";

function fields(): Target[] {
  return pageLabels.current().filter((t) => t.el.matches(FIELD));
}

function writeIn(host: CommandHost, t: Target): void {
  const d = destinationFor(t.el);
  if (!d) { host.notice({ text: `Cannot write in ${short(t.name)}` }); return; }
  (t.el as HTMLElement).focus({ preventScroll: false });
  host.setDestination(d);
  host.notice({ text: `Writing in ${short(d.name)}` });
}

/** "Write in search": words go to the field of that name from now on. */
export function writeInto(host: CommandHost, r: Run): void {
  const t = r.arg ? findTarget(fields(), r.arg) : null;
  if (t) writeIn(host, t);
  else notCommand(host, r, `No field called "${short(r.arg ?? "")}"`);
}

/** The fields on screen as numbered choices. */
export function chooseField(host: CommandHost): void {
  const list = fields();
  if (!list.length) { host.notice({ text: "No fields on screen" }); return; }
  numbered.show({
    kind: "choices",
    title: "Write in",
    groups: numberGroups([{ label: "Fields on screen", items: list.map((t) => ({ label: t.name || "Unnamed field", run: () => writeIn(host, t) })) }]),
  });
}
