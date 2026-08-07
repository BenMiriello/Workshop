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
  | { kind: "tool"; name?: string; input?: string; id?: string; agent?: string | null; parent?: string | null }
  | { kind: "text"; content?: string; parent?: string | null }
  // The Agent call's own result, which closes its segment.
  | { kind: "tool_result"; tool_use_id?: string | null; text?: string; parent?: string | null };

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
    step.t === "text" && last && last.t === "text"
      ? [...steps.slice(0, -1), { t: "text" as const, text: last.text + step.text }]
      : [...steps, step];
  const copy = segs.slice();
  copy[i] = { ...host, steps: nextSteps };
  return copy;
}

function pushText(segs: ChatSegment[], content: string): ChatSegment[] {
  if (!content) return segs;
  const last = segs[segs.length - 1];
  if (last && last.t === "text") {
    const copy = segs.slice();
    copy[copy.length - 1] = { t: "text", text: last.text + content };
    return copy;
  }
  return [...segs, { t: "text", text: content }];
}

/** Fold one stream event into the turn's segments. */
export function applyStreamEvent(segs: ChatSegment[], ev: StreamEvent): ChatSegment[] {
  const parent = ev.parent || "";
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
  if (ev.kind === "tool") {
    const step: ChatSegment = { t: "tool", name: ev.name || "?", input: ev.input || "" };
    if (parent) {
      const nested = intoAgent(segs, parent, step);
      if (nested) return nested;
    }
    if (ev.agent) {
      return [...segs, {
        t: "agent",
        name: ev.agent,
        input: ev.input || "",
        id: ev.id || "",
        steps: [],
        done: false,
        result: "",
      }];
    }
    return [...segs, step];
  }
  // tool_result: only a subagent's own result is rendered. Every other tool
  // result is plumbing the operator already sees the effect of.
  const id = ev.tool_use_id || "";
  if (!id) return segs;
  const i = segs.map((s, k) => (isAgent(s) && s.id === id ? k : -1)).filter((k) => k >= 0).pop();
  if (i === undefined) return segs;
  const copy = segs.slice();
  copy[i] = { ...(segs[i] as Extract<ChatSegment, { t: "agent" }>), done: true, result: ev.text || "" };
  return copy;
}

/** One line describing what a subagent did, for the collapsed chip. */
export function agentSummary(seg: Extract<ChatSegment, { t: "agent" }>): string {
  const tools = seg.steps.filter((s) => s.t === "tool").length;
  if (!seg.done) return tools ? `working · ${tools} step${tools > 1 ? "s" : ""}` : "working";
  return tools ? `${tools} step${tools > 1 ? "s" : ""}` : "done";
}
