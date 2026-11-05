// What a spoken command does to the page itself: go back (into the action
// history, so Cancel goes forward again), scroll the part of the page in the
// middle of the screen, click a control by its label's number, its name, its
// place or what it is (labels/clickResolve.ts), and pick a number from
// whatever is numbered.
// Scrolling and clicking are not in the history: a scroll is taken back by
// scrolling, and a click on an arbitrary control has nothing general to undo.

import { findIn } from "../../../lib/speech/spans";
import { rankControls } from "../../../lib/speech/uiMatchApi";
import { notCommand, recordAction, short, type NoticeHost } from "./actionRun";
import type { CommandHost } from "./dictation";
import { resolveClick } from "./labels/clickResolve";
import { findTarget, pressTarget, spokenNumber } from "./labels/pageTargets";
import { pageLabels } from "./labels/pageLabels";
import { describeAll, type Described } from "./labels/targetFacts";
import { numberGroups, numbered } from "./numbered";
import type { Said } from "./takeBacks";
import type { Run } from "./voiceActionTypes";

const STEP = 0.8;

export function goBack(host: NoticeHost, at: Said | null): void {
  history.back();
  recordAction(host, {
    label: "Went back",
    undo: async () => { history.forward(); return { ok: true, text: "Forward again" }; },
    redo: async () => { history.back(); return { ok: true, text: "Went back" }; },
  }, at);
}

export function goForward(host: NoticeHost, at: Said | null): void {
  history.forward();
  recordAction(host, {
    label: "Went forward",
    undo: async () => { history.back(); return { ok: true, text: "Back again" }; },
    redo: async () => { history.forward(); return { ok: true, text: "Went forward" }; },
  }, at);
}

function scrollable(el: Element | null): Element | null {
  for (let e = el; e; e = e.parentElement) {
    const s = getComputedStyle(e);
    if (/(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 1) return e;
  }
  return null;
}

// The scroller under the middle of the screen, looking into a same-origin
// frame there; the page's own scroller when nothing there scrolls.
export function scrollerAtCentre(): Element {
  const x = innerWidth / 2;
  const y = innerHeight / 2;
  let el = document.elementFromPoint(x, y);
  if (el instanceof HTMLIFrameElement) {
    try {
      const r = el.getBoundingClientRect();
      const inner = el.contentDocument?.elementFromPoint(x - r.left, y - r.top) ?? null;
      const found = scrollable(inner) ?? el.contentDocument?.scrollingElement ?? null;
      if (found && found.scrollHeight > found.clientHeight + 1) return found;
    } catch { /* another origin: scroll the shell */ }
  }
  el = scrollable(el) ?? document.querySelector(".ws-app__body > main");
  return el ?? document.scrollingElement ?? document.documentElement;
}

export function scrollPage(host: NoticeHost, down: boolean): void {
  const el = scrollerAtCentre();
  const before = el.scrollTop;
  el.scrollBy({ top: (down ? 1 : -1) * el.clientHeight * STEP, behavior: "smooth" });
  if (el.scrollHeight - el.clientHeight <= 0 || (down ? before >= el.scrollHeight - el.clientHeight - 1 : before <= 0)) {
    host.notice({ text: down ? "Already at the bottom" : "Already at the top" });
  }
}

/** Click what `query` names: a label's number, or a control's name. False
 *  when nothing on screen fits. */
export function clickNamed(host: NoticeHost, query: string): boolean {
  const targets = pageLabels.current();
  const t = findTarget(targets, query) ?? findIn(query, (q) => findTarget(targets, q));
  if (!t) return false;
  host.notice({ text: pressTarget(t) });
  return true;
}

function labelOf(t: Described): string {
  if (t.name) return t.name;
  if (t.icon) return `${t.icon} icon`;
  return `${t.image || t.kind === "image" ? "Picture" : t.kind[0].toUpperCase() + t.kind.slice(1)} ${t.n}`;
}

function press(host: NoticeHost, t: Described): void {
  host.notice({ text: pressTarget({ ...t, name: labelOf(t) }) });
}

/** Click what `said` means on screen, or offer the controls it could mean
 *  as numbered choices. False when nothing fits. */
export async function clickSaid(host: NoticeHost, said: string): Promise<boolean> {
  const targets = describeAll(pageLabels.current());
  const got = await resolveClick(targets, said, rankControls, { width: innerWidth, height: innerHeight });
  if (!got) return false;
  if ("press" in got) { press(host, got.press); return true; }
  const items = got.choose.map((t) => ({
    label: labelOf(t), hint: t.row || t.context || t.title || undefined, run: () => press(host, t),
  }));
  numbered.show({ kind: "choices", title: "Did you mean", groups: numberGroups([{ label: "On screen", items }]) });
  return true;
}

/** "Click <name>": the control it means, choices when several could be, or
 *  say nothing fits. */
export function clickTarget(host: CommandHost, r: Run): void {
  const said = r.arg ?? "";
  const missing = () => notCommand(host, r, `Nothing called "${short(said)}" on screen`);
  if (!said) { missing(); return; }
  void clickSaid(host, said).then((done) => { if (!done) missing(); });
}

/** "Number four": the item of a list showing, else the label. */
export function pickNumber(host: NoticeHost, said: string): void {
  const n = spokenNumber(said) ?? findIn(said, (q) => spokenNumber(q));
  if (n === null) { host.notice({ text: `No number in "${short(said)}"` }); return; }
  if (numbered.pick(n)) return;
  if (pageLabels.get().on && clickNamed(host, String(n))) return;
  host.notice({ text: "Nothing numbered to pick from" });
}
