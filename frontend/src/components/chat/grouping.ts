import type { ChatSegment } from "../../lib/chat";

// How a turn's work becomes the rows the transcript draws.
//
// THERE IS NO ACTIVITY REGION. A turn is an ordered list of BLOCKS: prose the
// model wrote, and runs of machine steps between them. The old shape put every
// non-answer segment into one `.chat-work` div, which was not a block type at
// all — it was whatever was left after the answer was taken off the end, so a
// turn that narrated mid-flight buried its own narration inside a work box.
//
// Prose blocks are the landmarks and runs are the collapsible interstitial
// between them. That is Linear's rule for issue activity, one level down, and
// it means the structure comes from the content instead of from a wrapper.
//
// Pure and total: no React, no dates, no mutation of the input. That is what
// makes the rule testable (grouping.test.ts) rather than only observable by
// watching a turn go by.
//
// See docs/decisions/0015-transcript-structure-and-content-provenance.md §1-2.

type ToolSegment = Extract<ChatSegment, { t: "tool" }>;

/** What a run of the same kind of step collapses to. The verb is what the
 *  summary line leads with; `noun` is counted after it. */
type ToolClass = { key: string; verb: string; noun: string };

// THE MERGE ALLOWLIST, and it is closed on purpose.
//
// Only read-only calls merge. A write or a command is the thing the operator
// most needs to see and is never folded into a count: microsoft/vscode issue 292985
// is the cost of getting this wrong, where `rm -rf important_dir` collapsed to
// "Executed command in terminal" and stayed that way.
const MERGEABLE: Record<string, ToolClass> = {
  Read: { key: "read", verb: "Read", noun: "file" },
  NotebookRead: { key: "read", verb: "Read", noun: "file" },
  Grep: { key: "grep", verb: "Searched", noun: "time" },
  Glob: { key: "glob", verb: "Matched", noun: "pattern" },
  WebFetch: { key: "fetch", verb: "Fetched", noun: "page" },
  WebSearch: { key: "websearch", verb: "Searched the web", noun: "time" },
};

/** Is this step still going? A tool carries `at` (when it started) from the
 *  moment it is issued and loses it when its result lands, so "open" is a fact
 *  about the segment rather than a guess from its position. The running step is
 *  never merged and never collapsed: it is the one thing the turn is doing. */
export function isRunning(seg: ChatSegment): boolean {
  return seg.t === "tool" && typeof seg.at === "number" && seg.ok === undefined;
}

/** Did it fail? `ok` is only absent on a step recorded before outcomes existed
 *  and on one whose turn ended before its result arrived. Neither is a failure,
 *  and treating them as one would paint the whole history red. */
export function isFailed(seg: ChatSegment): boolean {
  return seg.t === "tool" && seg.ok === false;
}

/** The class a step merges under, or null when it must stand alone.
 *
 *  Standing alone: anything not on the allowlist (every write and every
 *  command), anything that failed, and anything still running. */
export function mergeClass(seg: ChatSegment): ToolClass | null {
  if (seg.t !== "tool") return null;
  if (isFailed(seg) || isRunning(seg)) return null;
  return MERGEABLE[seg.name] ?? null;
}

export type TurnItem =
  // Two or more adjacent steps of one class, shown as a count and openable.
  | { t: "group"; cls: ToolClass; steps: ToolSegment[] }
  // One step on its own row: a write, a command, a failure, a subagent, an
  // approval, or the step currently running.
  | { t: "step"; seg: ChatSegment };

export type TurnBlock =
  | { t: "prose"; text: string }
  // A contiguous stretch of machine work between two prose blocks.
  | { t: "run"; items: TurnItem[] };

/** Split a turn's work into prose blocks and the runs of steps between them.
 *
 *  GROUPING IS INTERLEAVE-TOLERANT, and it has to be. "Merge consecutive steps
 *  of the same class" is the rule every product states, and it collapses to
 *  nothing on the commonest exploration shape there is: forty reads alternating
 *  with forty greps has no two adjacent steps of one class, so a strict reading
 *  produces eighty rows and the rule buys exactly zero.
 *
 *  So a WINDOW of groupable steps accumulates until something ungroupable ends
 *  it (a write, a command, a failure, a subagent, an approval, the running
 *  step), and each class in that window emits one row, in the order that class
 *  first appeared. Within a window the display order is by class rather than by
 *  time; across windows, time is preserved exactly. Nothing groupable can hop
 *  over a write or a command, which is what makes the reordering safe.
 */
export function buildTurnBlocks(work: ChatSegment[]): TurnBlock[] {
  const blocks: TurnBlock[] = [];
  let run: TurnItem[] = [];
  // The open window: class key -> its steps, in first-appearance order.
  let window: { cls: ToolClass; steps: ToolSegment[] }[] = [];

  const closeWindow = () => {
    for (const g of window) {
      // A group of one is not a group. "Read 1 file" hides the filename in
      // order to say nothing the row could not have said outright.
      run.push(g.steps.length === 1
        ? { t: "step", seg: g.steps[0] }
        : { t: "group", cls: g.cls, steps: g.steps });
    }
    window = [];
  };
  const closeRun = () => {
    closeWindow();
    if (run.length) blocks.push({ t: "run", items: run });
    run = [];
  };

  for (const seg of work) {
    if (seg.t === "text") {
      // Prose CLOSES a run, always. An acknowledgement written halfway through
      // a turn is the model speaking, and a landmark the reader navigates by;
      // merging steps across it would fold work from before and after one
      // statement into a single count that describes neither.
      closeRun();
      if (seg.text.trim()) blocks.push({ t: "prose", text: seg.text });
      continue;
    }
    const cls = mergeClass(seg);
    if (!cls) {
      closeWindow();
      run.push({ t: "step", seg });
      continue;
    }
    const open = window.find((g) => g.cls.key === cls.key);
    if (open) open.steps.push(seg as ToolSegment);
    else window.push({ cls, steps: [seg as ToolSegment] });
  }
  closeRun();
  return blocks;
}

/** One line for a whole turn's work, for a turn that is no longer the latest.
 *
 *  A settled turn shows THIS and nothing else until it is opened. Rendering
 *  every group of every historical turn would be a density regression on a long
 *  thread: today an old turn shows exactly one line for all of its work, and
 *  that is the right amount for something the reader has already read. */
export function turnWorkSummary(work: ChatSegment[]): string {
  let steps = 0;
  let wrote = 0;
  let ran = 0;
  let failed = 0;
  let agents = 0;
  for (const s of work) {
    if (s.t === "agent") { agents++; steps++; continue; }
    if (s.t !== "tool") continue;
    steps++;
    if (isFailed(s)) failed++;
    if (s.name === "Edit" || s.name === "Write" || s.name === "NotebookEdit") wrote++;
    else if (s.name === "Bash") ran++;
  }
  if (!steps) return "";
  const parts = [`${steps} step${steps === 1 ? "" : "s"}`];
  if (wrote) parts.push(`${wrote} edit${wrote === 1 ? "" : "s"}`);
  if (ran) parts.push(`${ran} command${ran === 1 ? "" : "s"}`);
  if (agents) parts.push(`${agents} agent${agents === 1 ? "" : "s"}`);
  // Failures are named in the collapsed line, because the whole reason to open
  // a settled turn is usually that something in it went wrong.
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ");
}

/** The collapsed summary of a merged group: verb, count, noun.
 *
 *  Verb + object + count, and the objects stay visible beside it (see
 *  `groupDetail`). A generic verb with the object hidden is exactly the failure
 *  mode this rule exists to avoid. */
export function groupSummary(item: Extract<TurnItem, { t: "group" }>): string {
  const n = item.steps.length;
  return `${item.cls.verb} ${n} ${item.cls.noun}${n === 1 ? "" : "s"}`;
}

/** The objects a merged group acted on, as one line. Clamped by CSS at the
 *  reader's real width rather than at a guessed character count, the same way
 *  every other one-line preview in the transcript is. */
export function groupDetail(item: Extract<TurnItem, { t: "group" }>): string {
  return item.steps
    .map((s) => (s.input || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(", ");
}

// How many rows a single run shows before the rest go behind one control.
//
// ONE expander, never a batched loader. GitHub's "N hidden items" is the
// documented counter-example: it reveals a few more per click and moves the
// link each time, so reaching the start of a long history takes dozens of
// clicks.
export const RUN_ROW_CAP = 8;

/** Rows kept inline, and how many are hidden behind the control.
 *
 *  THE CAP IS PER RUN, not per prose block. Per prose block is undefined for a
 *  turn that wrote no prose at all (common: one that ends on a write, or that
 *  the operator stopped), and it makes visible density a function of how chatty
 *  the model was, so the same work shows more rows when narrated than when not.
 *
 *  The tail is kept, not the head: the most recent work is what the reader is
 *  following. A run that is blocked or broken keeps the row that says so no
 *  matter where it sits, because hiding a pending approval hides the only
 *  control that can restart the turn, and hiding a failure hides why the turn
 *  did what it did next. */
export function splitRun(items: TurnItem[], cap = RUN_ROW_CAP): {
  shown: TurnItem[]; hidden: TurnItem[];
} {
  if (items.length <= cap) return { shown: items, hidden: [] };
  const mustShow = (i: TurnItem) =>
    i.t === "step" &&
    ((i.seg.t === "approval" && i.seg.state === "pending") || isFailed(i.seg) || isRunning(i.seg));
  const keep = new Set<number>();
  for (let i = items.length - 1; i >= 0 && keep.size < cap; i--) keep.add(i);
  items.forEach((i, k) => { if (mustShow(i)) keep.add(k); });
  const shown = items.filter((_, k) => keep.has(k));
  const hidden = items.filter((_, k) => !keep.has(k));
  return { shown, hidden };
}

/** How long a finished step took, for its collapsed row. Sub-second steps show
 *  nothing: a duration nobody was waiting on is noise on every row. */
export function stepDuration(seg: ChatSegment): string {
  if (seg.t !== "tool" || typeof seg.ms !== "number" || seg.ms < 1000) return "";
  const s = Math.round(seg.ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** How long a whole turn took, for the line that summarises its work.
 *
 *  Silent under 30 seconds. A total is only interesting if somebody waited for
 *  it, and printing one on every reply is a number per turn that nobody reads.
 *  Silent on a missing or nonsensical span too, rather than guessing. */
export function turnDuration(ms: number | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 30_000) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Seconds a still-running step has been going, given the current clock.
 *
 *  Under the 10s attention limit this returns 0 and nothing renders: a counter
 *  on a step that finishes immediately is flicker. Past it the row ticks, so a
 *  long tool call visibly runs instead of looking hung. */
export function runningSeconds(seg: ChatSegment, nowMs: number): number {
  if (!isRunning(seg) || seg.t !== "tool" || typeof seg.at !== "number") return 0;
  const secs = Math.floor((nowMs - seg.at * 1000) / 1000);
  return secs >= 10 ? secs : 0;
}
