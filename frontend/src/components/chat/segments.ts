import type { ChatSegment } from "../../lib/chat";

// How a live turn's SSE events become the segments the transcript renders.
//
// This mirrors the backend's build_streaming._segment_ingest, and it has to:
// the SAME turn is rendered from this reducer while it streams and from the
// server's persisted segments after a reload. If the two disagree, a subagent's
// steps rearrange themselves the moment you refresh.
//
// Pure and total: no React, no fetching, no mutation of the input. That is what
// makes the rule testable (segments.test.ts) rather than only observable by
// watching a real turn go by.

export type StreamEvent =
  // A tool call. `parent` is set when a SUBAGENT made it, and `agent` is set
  // when the call IS a subagent launch (the tool the CLI names `Agent`).
  // `detail` is the full brief behind a subagent launch — sent only for those,
  // and only ever shown when the chip is opened.
  | { kind: "tool"; name?: string; input?: string; detail?: string | null; id?: string; agent?: string | null; parent?: string | null }
  | { kind: "text"; content?: string; parent?: string | null }
  // A tool's result. For an Agent call it closes the subagent segment; for any
  // other top-level call it closes THAT STEP, setting its outcome and duration.
  // `is_error` is the field the whole disclosure rule turns on: a failed step is
  // never merged into a group and never collapses.
  | { kind: "tool_result"; tool_use_id?: string | null; text?: string; is_error?: boolean; parent?: string | null }
  // The turn is asking to use a tool and is BLOCKED until answered.
  | {
      kind: "permission";
      id?: string; tool?: string; label?: string; detail?: string;
      input?: Record<string, unknown>;
      suggestions?: { type?: string; mode?: string; destination?: string }[];
    }
  // An answer landed (from this client or another tab), or the turn ended with
  // requests still open — `stale` resolves every one of them at once.
  | { kind: "permission_resolved"; id?: string; state?: string; stale?: boolean }
  // A MESSAGE LANDED HERE. `ref` is its cut token, and the position it names is
  // the segment this arrives after. Handled by the reducer rather than by
  // applyStreamEvent, because the one position with no segment to sit on (the
  // turn has emitted nothing yet) lives on the run, not in the segment list.
  // See docs/decisions/0018.
  | { kind: "cut"; ref?: string };

const isAgent = (s: ChatSegment): s is Extract<ChatSegment, { t: "agent" }> => s.t === "agent";

/** Append `step` to the open subagent with this id. Returns null when no such
 *  subagent is open, which tells the caller to treat the event as ordinary
 *  top-level work rather than drop it. */
function intoAgent(segs: ChatSegment[], parent: string, step: ChatSegment): ChatSegment[] | null {
  const i = segs.map((s, k) => (isAgent(s) && s.id === parent ? k : -1)).filter((k) => k >= 0).pop();
  if (i === undefined) return null;
  const host = segs[i] as Extract<ChatSegment, { t: "agent" }>;
  const steps = host.steps;
  const last = steps[steps.length - 1];
  const nextSteps =
    step.t === "text" && last && last.t === "text" && !last.sealed
      ? [...steps.slice(0, -1), { t: "text" as const, text: last.text + step.text }]
      : [...steps, step];
  const copy = segs.slice();
  copy[i] = { ...host, steps: nextSteps };
  return copy;
}

function pushText(segs: ChatSegment[], content: string): ChatSegment[] {
  if (!content) return segs;
  const last = segs[segs.length - 1];
  if (last && last.t === "text" && !last.sealed) {
    const copy = segs.slice();
    copy[copy.length - 1] = { t: "text", text: last.text + content };
    return copy;
  }
  return [...segs, { t: "text", text: content }];
}

/** Close every open text run, because a message just landed here.
 *
 *  THE COUNTER IS NOT ENOUGH ON ITS OWN. Text streams into the trailing text
 *  segment rather than starting a new one, so "A", a message, then "B" is a
 *  single segment "AB" by the time the reply is cut, and the message renders
 *  below text written after it — the same misplacement the cut point exists to
 *  fix, arriving by a different route.
 *
 *  Mirrors the backend's build_streaming.seal_open_text, and the two run
 *  independently on the same trigger: the server seals the turn it is driving,
 *  every open tab seals when it sees the message land in the log. Neither needs
 *  to tell the other, which is what makes it work in a tab that did not type it. */
export function sealTrailingText(segs: ChatSegment[]): ChatSegment[] {
  const last = segs[segs.length - 1];
  if (!last) return segs;
  if (last.t === "text") {
    if (last.sealed) return segs;
    return [...segs.slice(0, -1), { ...last, sealed: true }];
  }
  if (last.t === "agent") {
    const step = last.steps[last.steps.length - 1];
    if (!step || step.t !== "text" || step.sealed) return segs;
    return [...segs.slice(0, -1), {
      ...last,
      steps: [...last.steps.slice(0, -1), { ...step, sealed: true }],
    }];
  }
  return segs;
}

/** Fold one stream event into the turn's segments. */
export function applyStreamEvent(segs: ChatSegment[], ev: StreamEvent): ChatSegment[] {
  // Permission events have no sidechain: a subagent cannot ask on its own
  // behalf, the parent turn's process is the one that blocks.
  const parent = ("parent" in ev && ev.parent) || "";
  if (ev.kind === "text") {
    const content = ev.content || "";
    if (parent) {
      // A subagent's interim text belongs to the subagent. Falling through to
      // pushText here is exactly how a delegate's working notes end up inside
      // the operator's answer.
      const nested = intoAgent(segs, parent, { t: "text", text: content });
      if (nested) return nested;
    }
    return pushText(segs, content);
  }
  if (ev.kind === "permission") {
    const id = ev.id || "";
    // The same request can arrive twice (a reconnect replays the tail); asking
    // twice for one decision would be worse than missing it.
    if (segs.some((s) => s.t === "approval" && s.id === id)) return segs;
    return [...segs, {
      t: "approval",
      id,
      tool: ev.tool || "?",
      label: ev.label || ev.tool || "?",
      detail: ev.detail || "",
      input: ev.input || {},
      suggestions: ev.suggestions || [],
      state: "pending",
    }];
  }
  if (ev.kind === "permission_resolved") {
    const next = ev.stale ? "stale" : (ev.state === "allowed" ? "allowed" : "denied");
    return segs.map((s) =>
      s.t !== "approval" || s.state !== "pending" ? s
        : (ev.stale || s.id === ev.id) ? { ...s, state: next as typeof s.state } : s);
  }
  if (ev.kind === "tool") {
    const step: ChatSegment = { t: "tool", name: ev.name || "?", input: ev.input || "" };
    if (parent) {
      // A DELEGATE'S STEP IS OPEN THE SAME WAY A TOP-LEVEL ONE IS. This used to
      // give nested steps neither `id` nor `at`, on the belief that a
      // subagent's tool results never reach this reducer. They do: a nested
      // result arrives with `parent` set. Without the id it matched nothing, so
      // every step inside an opened chip stayed outcome-less, and no outcome
      // reads as succeeded.
      if (!ev.agent && ev.id) {
        (step as Extract<ChatSegment, { t: "tool" }>).id = ev.id;
        (step as Extract<ChatSegment, { t: "tool" }>).at = Date.now() / 1000;
      }
      const nested = intoAgent(segs, parent, step);
      if (nested) return nested;
    }
    // A TOP-LEVEL call is open until its result lands: `id` is how the result
    // finds it, and `at` is both the start of its clock and the fact that it is
    // still going. Mirrors the backend's _seg_tool, which persists the same two
    // fields, so a reload mid-call keeps the same row in the same state.
    if (!ev.agent && ev.id) {
      step.name = ev.name || "?";
      (step as Extract<ChatSegment, { t: "tool" }>).id = ev.id;
      (step as Extract<ChatSegment, { t: "tool" }>).at = Date.now() / 1000;
    }
    if (ev.agent) {
      return [...segs, {
        t: "agent",
        name: ev.agent,
        input: ev.input || "",
        ...(ev.detail ? { detail: ev.detail } : {}),
        id: ev.id || "",
        steps: [],
        done: false,
        result: "",
      }];
    }
    return [...segs, step];
  }
  // A CUT IS A POSITION, NOT CONTENT, so it is not folded into the segments
  // here. The reducer records it, because the one position with no segment to
  // sit on (the turn has emitted nothing yet) lives on the run rather than in
  // this list. Returning `segs` unchanged is what tells the reducer's identity
  // check that nothing was folded. See threadStore's runDelta.
  if (ev.kind === "cut") return segs;
  // tool_result: a subagent's result is RENDERED (it is the report the chip
  // opens onto). Every other result closes its own step without being shown —
  // what it contributes is the outcome and the duration, which is what lets a
  // settled turn tell a command that failed from a file that was read.
  const id = ev.tool_use_id || "";
  if (!id) return segs;
  const i = segs.map((s, k) => (isAgent(s) && s.id === id ? k : -1)).filter((k) => k >= 0).pop();
  if (i !== undefined) {
    const copy = segs.slice();
    copy[i] = { ...(segs[i] as Extract<ChatSegment, { t: "agent" }>), done: true, result: ev.text || "" };
    return copy;
  }
  const j = segs.map((s, k) => (s.t === "tool" && s.id === id ? k : -1)).filter((k) => k >= 0).pop();
  if (j !== undefined) {
    const out = segs.slice();
    out[j] = closeStep(segs[j] as Extract<ChatSegment, { t: "tool" }>, ev);
    return out;
  }
  // Not top-level, so look inside the delegations. A subagent's step closes
  // exactly like the parent's own, which is what lets an opened chip tell a
  // search that failed from one that came back empty.
  for (let k = segs.length - 1; k >= 0; k--) {
    const host = segs[k];
    if (!isAgent(host)) continue;
    const s = host.steps.findIndex((x) => x.t === "tool" && x.id === id);
    if (s < 0) continue;
    const steps = host.steps.slice();
    steps[s] = closeStep(steps[s] as Extract<ChatSegment, { t: "tool" }>, ev);
    const out = segs.slice();
    out[k] = { ...host, steps };
    return out;
  }
  return segs;
}

/** Settle one open tool step: its outcome, how long it ran, and its failure
 *  text. `at` is dropped, because it is what marks a step as still running and
 *  a finished step that kept it would tick forever. */
function closeStep(
  open: Extract<ChatSegment, { t: "tool" }>,
  ev: { is_error?: boolean; text?: string },
): Extract<ChatSegment, { t: "tool" }> {
  const closed: Extract<ChatSegment, { t: "tool" }> = { ...open, ok: !ev.is_error };
  if (typeof open.at === "number") closed.ms = Math.max(0, Math.round(Date.now() - open.at * 1000));
  delete closed.at;
  if (ev.is_error && ev.text) closed.error = ev.text;
  return closed;
}

type AgentSegment = Extract<ChatSegment, { t: "agent" }>;

/** THE ONE LINE a collapsed subagent chip shows: what it was asked to do.
 *
 *  A delegation is one step of the parent's work, so its chip is one row. The
 *  brief it was handed is a page of instructions and belongs behind the chip,
 *  not on it — a chip that unrolled the whole brief was the loudest thing in a
 *  turn while saying the least about it.
 *
 *  Newlines are folded to spaces HERE rather than left to CSS: the string is
 *  what must be one line, so that a segment recorded before the backend started
 *  sending a one-line `input` still cannot wrap. Truncation stays with CSS
 *  (`text-overflow: ellipsis`), which cuts at the width the reader actually
 *  has instead of at a guessed character count. */
export function agentChipLine(seg: AgentSegment): string {
  const raw = (seg.input || "").replace(/\s+/g, " ").trim();
  return raw || seg.name;
}

/** What opening the chip reveals: the brief the delegate was actually handed.
 *
 *  Empty when there is nothing more to show than the row already says — an
 *  older segment with no `detail`, or a `detail` that is just the chip line
 *  again. A disclosure control that opens onto a repeat of its own label is
 *  worse than no control. */
export function agentBrief(seg: AgentSegment): string {
  const detail = (seg.detail || "").trim();
  if (!detail) return "";
  return detail.replace(/\s+/g, " ").trim() === agentChipLine(seg) ? "" : detail;
}

/** One line describing what a subagent did, for the collapsed chip.
 *
 *  "working" is claimed only while the turn is actually running. A delegate is
 *  killed with its parent, so a stopped or lost turn closes its open subagents
 *  with an `outcome` and the chip says which — it used to keep reading "working"
 *  for the rest of the thread's life, describing a process that had been dead
 *  since the moment the operator pressed Stop. */
export function agentSummary(seg: AgentSegment): string {
  const tools = seg.steps.filter((s) => s.t === "tool").length;
  const steps = tools ? `${tools} step${tools > 1 ? "s" : ""}` : "";
  if (!seg.done) return steps ? `working · ${steps}` : "working";
  if (seg.outcome) {
    const why = seg.outcome === "unfinished" ? "did not finish" : seg.outcome;
    return steps ? `${why} · ${steps}` : why;
  }
  return steps || "done";
}
