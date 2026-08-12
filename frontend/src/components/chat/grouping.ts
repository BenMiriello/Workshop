import type { ChatSegment } from "../../lib/chat";

// How a turn's work becomes the rows the transcript draws.
//
// THERE IS NO ACTIVITY REGION. A turn is an ordered list of BLOCKS: prose the
// model wrote, and runs of machine steps between them. The old shape put every
// non-answer segment into one `.chat-work` div, which was not a block type at
// all — it was whatever was left after the answer was taken off the end, so a
// turn that narrated mid-flight buried its own narration inside a work box.
//
// GROUPING IS ADJACENT COALESCING BY PART TYPE, and nothing else. Walk the
// segments in order; consecutive `tool` parts become one group; any other part
// type ends it. This is the rule every reference implementation uses and it is
// deliberately the whole rule:
//
//   assistant-ui  MessagePrimitive.GroupedParts / ToolGroup: "adjacent parts
//                 that share a group key are coalesced", key derived from
//                 part.type via groupPartByType. Trigger shows a count.
//   AI Elements   <Tool>: one collapsible per call, header is name + status.
//   VS Code       chat.agent.thinking.collapsedTools: none | always |
//                 withThinking. Positional, not semantic.
//
// WHAT THIS REPLACED, AND WHY IT IS GONE. An earlier version of this file
// classified each step ("is this shell command read-only?", `bashClass.ts`) and
// merged by class through an interleave-tolerant window. Three things were
// wrong with it, all measured over the operator's real logs (419 turns, 13,320
// tool steps):
//
//   1. It REORDERED work. To make "consecutive same class" produce anything on
//      alternating reads and greps, the window emitted one row per class in
//      first-appearance order, so 282 of 4,296 runs (6.6%, 397 inversions)
//      displayed steps out of the order they happened. No product does this and
//      a transcript that lies about order is worse than a long one.
//   2. It compressed LESS: 24.4% fewer rows against this rule's 68.0%.
//   3. It PARAPHRASED. A group summarised as "Edited 5 files, ran 12 commands"
//      is what drew anthropics/claude-code#46110, and the same instinct one
//      step further is microsoft/vscode#292985, where `rm -rf important_dir`
//      collapsed to "Executed command in terminal".
//
// (3) is why the group header here is a COUNT and not a description. A count
// cannot misdescribe what it counted, so no merge allowlist is needed to keep a
// destructive command out of a group: opening the group shows every step, in
// order, individually, exactly as it happened.
//
// Pure and total: no React, no dates, no mutation of the input. That is what
// makes the rule testable (grouping.test.ts) rather than only observable by
// watching a turn go by.
//
// See docs/decisions/0015-transcript-structure-and-content-provenance.md §1-2.

type ToolSegment = Extract<ChatSegment, { t: "tool" }>;

/** Is this step still going? A tool carries `at` (when it started) from the
 *  moment it is issued and loses it when its result lands, so "open" is a fact
 *  about the segment rather than a guess from its position. */
export function isRunning(seg: ChatSegment): boolean {
  return seg.t === "tool" && typeof seg.at === "number" && seg.ok === undefined;
}

/** Did it fail? `ok` is only absent on a step recorded before outcomes existed
 *  and on one whose turn ended before its result arrived. Neither is a failure,
 *  and treating them as one would paint the whole history red. */
export function isFailed(seg: ChatSegment): boolean {
  return seg.t === "tool" && seg.ok === false;
}

export type TurnItem =
  // Two or more adjacent tool calls, behind one count.
  | { t: "group"; steps: ToolSegment[] }
  // One row on its own: a lone tool call, a subagent, or an approval.
  | { t: "step"; seg: ChatSegment };

export type TurnBlock =
  | { t: "prose"; text: string }
  // A contiguous stretch of machine work between two prose blocks.
  | { t: "run"; items: TurnItem[] };

/** Split a turn's work into prose blocks and the runs of steps between them.
 *
 *  Adjacent `tool` segments coalesce. `text`, `agent` and `approval` each break
 *  the group, because they are different part types: prose is the model
 *  speaking and a landmark the reader navigates by, a subagent is the one step
 *  that contains other steps, and an approval is a control the reader may need
 *  to act on. None of the three may be folded into a count of tool calls. */
export function buildTurnBlocks(work: ChatSegment[]): TurnBlock[] {
  const blocks: TurnBlock[] = [];
  let run: TurnItem[] = [];
  let open: ToolSegment[] = [];

  const closeGroup = () => {
    if (!open.length) return;
    // A group of one is not a group. One tool call renders as the row it
    // already is, rather than as a count of itself that hides its own name.
    run.push(open.length === 1 ? { t: "step", seg: open[0] } : { t: "group", steps: open });
    open = [];
  };
  const closeRun = () => {
    closeGroup();
    if (run.length) blocks.push({ t: "run", items: run });
    run = [];
  };

  for (const seg of work) {
    if (seg.t === "text") {
      closeRun();
      if (seg.text.trim()) blocks.push({ t: "prose", text: seg.text });
      continue;
    }
    if (seg.t === "tool") {
      open.push(seg);
      continue;
    }
    closeGroup();
    run.push({ t: "step", seg });
  }
  closeRun();
  return blocks;
}

/** The collapsed label for a group: how many steps, and how many went wrong.
 *
 *  A COUNT, NEVER A DESCRIPTION. See the header note: a paraphrase is the one
 *  thing a collapsed group must not do, because it can be wrong about what it
 *  hid. Failures are named because they are the usual reason to open it. */
export function groupSummary(item: Extract<TurnItem, { t: "group" }>): string {
  const n = item.steps.length;
  const failed = item.steps.filter(isFailed).length;
  const base = `${n} step${n === 1 ? "" : "s"}`;
  return failed ? `${base}, ${failed} failed` : base;
}

/** The distinct tool names in a group, in the order they first appear.
 *
 *  Names, not a summary of what they did. "Bash, Read, Grep" is a fact about
 *  the group that stays true however the steps are read; "Explored the repo" is
 *  a guess. This is the same thing AI Elements puts in a single Tool header,
 *  applied to a group of them. */
export function groupTools(item: Extract<TurnItem, { t: "group" }>): string {
  const seen: string[] = [];
  for (const s of item.steps) if (s.name && !seen.includes(s.name)) seen.push(s.name);
  return seen.join(", ");
}

/** Is anything in this group still running? A group holding the step the turn
 *  is on opens itself, which is assistant-ui's "auto-expand during streaming"
 *  and the reason collapsing by default costs nothing: you always see the work
 *  that is actually happening, and only settled work folds away. */
export function groupIsRunning(item: Extract<TurnItem, { t: "group" }>): boolean {
  return item.steps.some(isRunning);
}

/** One line for a whole turn's work, for a turn that is no longer the latest.
 *
 *  A settled turn shows THIS and nothing else until it is opened. Rendering
 *  every group of every historical turn would be a density regression on a long
 *  thread: today an old turn shows exactly one line for all of its work, and
 *  that is the right amount for something the reader has already read. */
export function turnWorkSummary(work: ChatSegment[]): string {
  let steps = 0;
  let failed = 0;
  let agents = 0;
  let asked = 0;
  for (const s of work) {
    if (s.t === "agent") { agents++; steps++; continue; }
    // An APPROVAL is a step. Counting only tools is what made a turn whose
    // whole body was a note to the operator and a permission it was refused
    // summarise to nothing, and a settled turn shows only this line.
    if (s.t === "approval") { asked++; steps++; continue; }
    if (s.t !== "tool") continue;
    steps++;
    if (isFailed(s)) failed++;
  }
  // "" only when there is genuinely nothing to count. A turn's work never
  // consists of prose alone (the trailing text run is split off as the answer
  // before this is called), and SettledWork carries its own fallback label
  // rather than trusting that to stay true.
  if (!steps) return "";
  const parts = [`${steps} step${steps === 1 ? "" : "s"}`];
  if (agents) parts.push(`${agents} agent${agents === 1 ? "" : "s"}`);
  if (asked) parts.push(`${asked} approval${asked === 1 ? "" : "s"}`);
  // Failures are named in the collapsed line, because the whole reason to open
  // a settled turn is usually that something in it went wrong.
  if (failed) parts.push(`${failed} failed`);
  return parts.join(" · ");
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
