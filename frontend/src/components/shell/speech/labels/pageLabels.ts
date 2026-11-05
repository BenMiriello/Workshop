// Whether the page's controls are shown numbered, and which ones, as a small
// external store (useSyncExternalStore). While on, the numbers follow the page:
// any scroll, resize or change in the shell or a shown frame recollects them,
// at most once an animation frame. The one the pointer is over is `hover`,
// so its badge can show the name it is said by. Off, nothing listens.

import { queryClient } from "../../../../lib/queryClient";
import { speechCommandsQuery } from "../../../../lib/speech/commandsApi";
import { watchDocuments } from "../frames";
import { collectTargets, shapeOf, type Target } from "./pageTargets";

export type LabelsState = { on: boolean; targets: Target[]; hover: number | null };

const OFF: LabelsState = { on: false, targets: [], hover: null };

let state = OFF;
const listeners = new Set<() => void>();
let frame = 0;
let detach: (() => void) | null = null;

function set(next: LabelsState) {
  state = next;
  listeners.forEach((fn) => fn());
}

function same(a: Target[], b: Target[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((t, i) => {
    const u = b[i];
    return t.el === u.el && t.name === u.name
      && Math.round(t.box.left) === Math.round(u.box.left) && Math.round(t.box.top) === Math.round(u.box.top);
  });
}

function recollect() {
  frame = 0;
  if (!state.on) return;
  const targets = collectTargets();
  if (same(targets, state.targets)) return;
  const was = state.targets.find((t) => t.n === state.hover)?.el;
  set({ on: true, targets, hover: targets.find((t) => t.el === was)?.n ?? null });
}

function schedule() {
  if (!frame && state.on) frame = requestAnimationFrame(recollect);
}

// The layer redraws its own badges on every recollect; those changes must
// not schedule another one.
function inLayer(r: MutationRecord): boolean {
  const node = r.target;
  const el = node.nodeType === 1 ? node as Element : node.parentElement;
  return !!el?.closest(".ws-labels");
}

// The innermost target holding `node`, as drawn (a switch is its label): a
// control inside a card that clicks is the control.
function targetAt(node: Node): number | null {
  let best: Target | null = null;
  for (const t of state.targets) {
    if (shapeOf(t.el).contains(node) && (!best || shapeOf(best.el).contains(t.el))) best = t;
  }
  return best?.n ?? null;
}

// Over: the target under the pointer. Out to nowhere (off the window, or
// into another document, whose own "over" follows): none.
function pointed(e: Event) {
  const p = e as PointerEvent;
  if (p.type === "pointerout" && p.relatedTarget) return;
  const hover = p.type === "pointerover" && p.target ? targetAt(p.target as Node) : null;
  if (hover !== state.hover) set({ ...state, hover });
}

function attach(doc: Document): () => void {
  const win = doc.defaultView;
  const Observer = win?.MutationObserver ?? MutationObserver;
  const observer = new Observer((records) => {
    if (!records.every(inLayer)) schedule();
  });
  observer.observe(doc.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  const opts = { capture: true, passive: true };
  doc.addEventListener("scroll", schedule, opts);
  doc.addEventListener("pointerover", pointed, opts);
  doc.addEventListener("pointerout", pointed, opts);
  win?.addEventListener("resize", schedule);
  // A newly loaded frame brings controls of its own.
  schedule();
  return () => {
    observer.disconnect();
    doc.removeEventListener("scroll", schedule, opts);
    doc.removeEventListener("pointerover", pointed, opts);
    doc.removeEventListener("pointerout", pointed, opts);
    win?.removeEventListener("resize", schedule);
  };
}

function watch(): () => void {
  const offDocs = watchDocuments(attach);
  const vv = window.visualViewport;
  vv?.addEventListener("resize", schedule);
  vv?.addEventListener("scroll", schedule);
  return () => {
    offDocs();
    vv?.removeEventListener("resize", schedule);
    vv?.removeEventListener("scroll", schedule);
  };
}

export const pageLabels = {
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  get: (): LabelsState => state,
  show(): void {
    if (state.on) return;
    void queryClient.prefetchQuery(speechCommandsQuery).then(schedule);
    set({ on: true, targets: collectTargets(), hover: null });
    detach = watch();
  },
  hide(): void {
    if (!state.on) return;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    detach?.();
    detach = null;
    set(OFF);
  },
  toggle(): void {
    if (state.on) pageLabels.hide();
    else pageLabels.show();
  },
  /** The targets as numbered now. Off, they are collected on the spot, so a
   *  control can be named without the numbers being shown. */
  current(): Target[] {
    return state.on ? state.targets : collectTargets();
  },
};
