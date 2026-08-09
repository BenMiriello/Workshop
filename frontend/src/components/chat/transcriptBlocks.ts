import type { ChatMsg, ChatSegment } from "../../lib/chat";
import {
  committedEvents, pendingSends,
  type LogEvent, type PendingStatus, type ThreadState,
} from "./threadStore";

// The transcript, DERIVED. Committed events in seq order, then the live run,
// then pending sends. Nothing is stored in this shape; it is recomputed from the
// store, which is what makes it impossible for two owners to disagree about it.
//
// ---------------------------------------------------------------------------
// A MESSAGE SENT MID-TURN CLOSES THE BLOCK IT LANDED IN.
// ---------------------------------------------------------------------------
//
// A reply is not one thing that happened at one moment. It is a run of work over
// an interval, and a message sent during that interval divides it: everything
// the turn had already emitted came BEFORE the message and could not have
// accounted for it; everything after was produced by a turn that had been
// handed it.
//
// So an assistant block is a CONTIGUOUS RUN OF WORK, bounded by the messages
// that interrupted it. Five messages sent during a long turn produce up to six
// assistant blocks, each sitting where its work actually happened.
//
// `meta.at_segment` — recorded by the server when the message was filed, read
// off the LIVE run rather than its throttled snapshot — is the cut point: the
// index into the reply's segment stream at which the message arrived. Splitting
// there puts the message AFTER work already emitted and BEFORE work not yet
// emitted, which is the only placement that is true.
//
// WHY THE OLD RULE SAID THE OPPOSITE. An earlier scheme also cut the reply open,
// and it was wrong: it placed the message ABOVE work that had already begun (in
// the worst case above the working indicator itself, when nothing had streamed
// yet), so the operator's words appeared to precede work that was started
// without them. That got corrected to "append-only, in send order: a mid-turn
// message renders BELOW the whole reply" — which fixed the placement and broke
// the reading, because the reply that finally answered five messages rendered
// above all five and read as ignoring them. Cutting at `at_segment` is neither:
// the message goes exactly where it landed. See frontend/CLAUDE.md.

export type TranscriptBlock =
  // A message as the log holds it: the operator's, or an assistant reply that
  // nothing interrupted.
  | { kind: "message"; key: string; seq: number; msg: ChatMsg }
  // The turn in flight. `working` marks the block the indicator belongs on —
  // the last one, because that is where the work is still happening.
  | { kind: "live"; key: string; runId: string; segments: ChatSegment[]; working: boolean }
  // Sent by this browser, not in the log yet.
  | { kind: "pending"; key: string; clientId: string; text: string; status: PendingStatus }
  // The context boundary a /compact or /clear left behind.
  | { kind: "cut"; key: string; count: number; cleared: boolean };

/** Split a segment list at the given indices. Cuts are clamped into range and
 *  forced non-decreasing, so a stale or out-of-order `at_segment` can only make
 *  a block empty, never reorder one. Returns cuts.length + 1 slices. */
export function splitSegments(segments: ChatSegment[], cuts: number[]): ChatSegment[][] {
  const out: ChatSegment[][] = [];
  let prev = 0;
  for (const raw of cuts) {
    const at = Math.max(prev, Math.min(Number.isFinite(raw) ? raw : segments.length, segments.length));
    out.push(segments.slice(prev, at));
    prev = at;
  }
  out.push(segments.slice(prev));
  return out;
}

function msgFromEvent(ev: LogEvent): ChatMsg {
  return {
    role: ev.role === "assistant" ? "assistant" : "user",
    content: ev.content || "",
    ts: ev.ts,
    ...(ev.meta ? { meta: ev.meta } : {}),
  };
}

/** The top-level text of a slice — what that block's answer says. */
function textOf(segments: ChatSegment[]): string {
  return segments.filter((s) => s.t === "text").map((s) => (s as { text: string }).text).join("").trim();
}

/** One committed reply, cut by the messages that interrupted it.
 *
 *  A reply nobody interrupted comes back as itself, untouched — the ordinary
 *  case must render exactly as it did before this rule existed. */
function replyBlocks(ev: LogEvent, interrupts: LogEvent[]): TranscriptBlock[] {
  const msg = msgFromEvent(ev);
  if (!interrupts.length) {
    return [{ kind: "message", key: ev.id, seq: ev.seq, msg }];
  }
  const segments = msg.meta?.segments || [];
  const parts = splitSegments(segments, interrupts.map((m) => m.meta?.at_segment ?? segments.length));
  const out: TranscriptBlock[] = [];
  let emitted = 0;
  const lastPart = parts.length - 1;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (part.length) {
      // Only the final block carries how the TURN ended (its notes, its timeout
      // or interrupted tag, when it finished). Those describe the end of the
      // run, not the end of every stretch of work inside it.
      const tail = i === lastPart;
      const meta = { ...msg.meta, segments: part };
      if (!tail) {
        delete meta.notes;
        delete meta.timeout;
        delete meta.stopped;
        delete meta.ended_at;
        delete meta.status;
        // `error` is the loudest of these and was the one left behind. It makes
        // MessageList short-circuit past the segments entirely and paint a red
        // bubble, so an errored reply the operator had interrupted rendered as N
        // red bubbles instead of its work plus one failure at the end.
        delete meta.error;
      }
      out.push({
        kind: "message",
        key: `${ev.id}:${i}`,
        seq: ev.seq,
        msg: { ...msg, content: textOf(part), meta },
      });
      emitted++;
    }
    if (i < interrupts.length) {
      const m = interrupts[i];
      out.push({ kind: "message", key: m.id, seq: m.seq, msg: msgFromEvent(m) });
    }
  }
  if (!emitted) {
    // Nothing had streamed when the messages landed, so every slice is empty and
    // the whole reply came after all of them. Say so by putting it last.
    out.push({ kind: "message", key: `${ev.id}:0`, seq: ev.seq, msg });
  }
  return out;
}

/** The run in flight, cut the same way. The last block is the live one: it is
 *  where the working indicator goes, and it is emitted even when empty, because
 *  "a turn is running and has produced nothing yet" still has to be visible. */
function liveBlocks(
  runId: string, segments: ChatSegment[], interrupts: LogEvent[],
): TranscriptBlock[] {
  const parts = splitSegments(segments, interrupts.map((m) => m.meta?.at_segment ?? segments.length));
  const out: TranscriptBlock[] = [];
  const lastPart = parts.length - 1;
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].length || i === lastPart) {
      out.push({
        kind: "live",
        key: `${runId}:${i}`,
        runId,
        segments: parts[i],
        working: i === lastPart,
      });
    }
    if (i < interrupts.length) {
      const m = interrupts[i];
      out.push({ kind: "message", key: m.id, seq: m.seq, msg: msgFromEvent(m) });
    }
  }
  return out;
}

/**
 * The whole transcript: committed log, the live run, then pending sends.
 *
 * `queued` is the type-ahead queue — messages the operator has hit enter on that
 * have not been sent at all yet. They belong at the very end, below everything
 * including the live turn, because that is the truthful place for something not
 * yet sent.
 */
export function selectTranscript(state: ThreadState): TranscriptBlock[] {
  const events = committedEvents(state);
  const activeRunId = state.activeRun?.runId ?? null;

  // Which run each committed reply belongs to, and where it sits. A mid-run
  // message is held back only when the block it interrupted is still ahead of
  // it (or is the live run); otherwise it renders exactly where it is in the
  // log, which is what a message filed against an already-finished run wants.
  const replySeqByRun = new Map<string, number>();
  for (const ev of events) {
    if (ev.kind !== "message" || ev.role !== "assistant") continue;
    const rid = ev.meta?.turn_id;
    if (rid && !replySeqByRun.has(rid)) replySeqByRun.set(rid, ev.seq);
  }

  const held = new Map<string, LogEvent[]>();
  const hold = (rid: string, ev: LogEvent) => {
    const list = held.get(rid);
    if (list) list.push(ev);
    else held.set(rid, [ev]);
  };

  const out: TranscriptBlock[] = [];
  // Only the latest boundary draws a divider, and it draws where the boundary
  // event sits in the log — which is after everything it cut.
  let cutAt = -1;
  let cutCount = 0;
  let cutCleared = false;
  let messagesSoFar = 0;

  for (const ev of events) {
    if (ev.kind === "boundary") {
      cutAt = out.length;
      cutCount = messagesSoFar;
      cutCleared = ev.mode === "clear";
      continue;
    }
    if (ev.kind !== "message") continue;
    messagesSoFar++;
    if (ev.role === "user") {
      const rid = ev.meta?.during_run;
      if (rid && (rid === activeRunId || (replySeqByRun.get(rid) ?? -1) > ev.seq)) {
        hold(rid, ev);
        continue;
      }
      out.push({ kind: "message", key: ev.id, seq: ev.seq, msg: msgFromEvent(ev) });
      continue;
    }
    const rid = ev.meta?.turn_id;
    const interrupts = rid ? (held.get(rid) || []) : [];
    if (rid) held.delete(rid);
    out.push(...replyBlocks(ev, interrupts));
  }

  if (cutAt >= 0 && cutCount > 0) {
    out.splice(cutAt, 0, {
      kind: "cut", key: `cut:${cutCount}:${cutCleared}`, count: cutCount, cleared: cutCleared,
    });
  }

  if (state.activeRun) {
    out.push(...liveBlocks(
      state.activeRun.runId, state.activeRun.segments, held.get(state.activeRun.runId) || [],
    ));
  }

  for (const p of pendingSends(state)) {
    out.push({
      kind: "pending", key: `p:${p.clientId}`, clientId: p.clientId, text: p.text, status: p.status,
    });
  }

  return out;
}

/** Every committed message, flat, in log order. The context meter's local
 *  estimate and the "was this the first exchange" check read this; nothing
 *  renders from it. */
export function committedMessages(state: ThreadState): ChatMsg[] {
  return committedEvents(state)
    .filter((ev) => ev.kind === "message")
    .map(msgFromEvent);
}
