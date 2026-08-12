import type { ChatMsg, ChatSegment } from "../../lib/chat";
import {
  committedEvents, lastSeq, pendingSends,
  type LogEvent, type PendingSend, type PendingStatus, type ThreadState,
} from "./threadStore";
import { cutPointOf, splitAtUnits } from "./transcriptUnits";

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
// `meta.at_unit` — recorded by the server when the message was filed, read off
// the LIVE run rather than its throttled snapshot — is the cut point: the index
// into the reply's stream of RENDERED UNITS at which the message arrived.
// Splitting there puts the message AFTER work already emitted and BEFORE work
// not yet emitted, which is the only placement that is true.
//
// It counts units and not top-level segments because a subagent's steps nest
// inside one segment, so a segment count stands still for the whole of a
// delegation — the operator reported the resulting misplacement three times
// before it was measured. See transcriptUnits.ts for the numbers.
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
  // Sent by this browser, not in the log yet. `error` is set only on `failed`,
  // and it renders UNDER this bubble: the failure belongs to this message, not
  // to the chat's one error slot above the composer.
  | {
      kind: "pending"; key: string; clientId: string; text: string;
      status: PendingStatus; error?: string;
      // Files this send carries. Rendered on the in-flight bubble too, so an
      // image you just sent shows its chip immediately rather than appearing
      // out of nowhere the moment the server commits the message.
      attachments?: PendingSend["attachments"];
    }
  // The context boundary a /compact or /clear left behind.
  | { kind: "cut"; key: string; count: number; cleared: boolean };

/** Split a segment list at the cut points of the messages that interrupted it.
 *  The coordinate is a UNIT index, not a segment index — see transcriptUnits.ts
 *  for why counting top-level segments alone froze the coordinate for the whole
 *  of a subagent delegation. */
function cutReply(segments: ChatSegment[], interrupts: LogEvent[]): ChatSegment[][] {
  return splitAtUnits(segments, interrupts.map((m) => cutPointOf(m.meta, segments)));
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
  const parts = cutReply(segments, interrupts);
  const out: TranscriptBlock[] = [];
  let emitted = 0;
  // HOW THE TURN ENDED GOES ON THE LAST BLOCK THAT EXISTS, not on the last
  // SLICE. An empty slice emits no block, so keying the ending to `parts.length
  // - 1` dropped it entirely whenever the final slice was empty — and that is
  // the common shape, not an edge case: a message sent and then Stop pressed, a
  // message that lands after the turn's last output, or (now that a delegation
  // is atomic) any message landing inside the final subagent, which snaps the
  // cut to the very end. The turn's error, stopped and timeout tags, its
  // receipts, its status note and its duration all live in that metadata, so an
  // errored reply rendered as an ordinary one and "Stopped by operator" never
  // appeared.
  let lastPart = -1;
  for (let i = 0; i < parts.length; i++) if (parts[i].length) lastPart = i;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (part.length) {
      // Only the final block carries how the TURN ended (its notes, its timeout
      // or interrupted tag, when it finished). Those describe the end of the
      // run, not the end of every stretch of work inside it.
      const tail = i === lastPart;
      const meta = { ...msg.meta, segments: part };
      // NO TOTAL ON A SPLIT REPLY. A duration is a property of the whole turn
      // and every block here carries the same `started_at`, so pairing it with
      // a summary counting only THIS block's steps printed things like
      // "2 steps · 3h 41m". The pair is computed only when both ends are known,
      // so dropping the start suppresses it. Everything below this line is the
      // split path: an uninterrupted reply returned above.
      delete meta.started_at;
      if (!tail) {
        delete meta.notes;
        // Same reason as `notes`, which this replaced: a receipt is what the
        // whole TURN changed outside the chat, so it belongs once, at the end.
        // Left in, a turn that wrote a note and was then interrupted repeated
        // "Wrote 1 note" on every stretch of work in it.
        delete meta.receipts;
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
  const parts = cutReply(segments, interrupts);
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

/** The committed transcript, and the mid-run messages it is deliberately NOT
 *  rendering yet because they belong inside the run still in flight. Splitting
 *  these out is what lets a streamed frame skip re-deriving the committed half;
 *  see `selectCommitted`. */
export type CommittedTranscript = {
  blocks: TranscriptBlock[];
  heldForActiveRun: LogEvent[];
};

/**
 * The whole transcript: committed log, the live run, then pending sends.
 *
 * `queued` is the type-ahead queue — messages the operator has hit enter on that
 * have not been sent at all yet. They belong at the very end, below everything
 * including the live turn, because that is the truthful place for something not
 * yet sent.
 */
export function selectTranscript(state: ThreadState): TranscriptBlock[] {
  return composeTranscript(selectCommitted(state), state);
}

/** The committed half of the transcript, plus whatever it is holding back for
 *  the run in flight.
 *
 *  THE SPLIT IS ABOUT WHEN THINGS CHANGE, not about tidiness. The committed log
 *  moves when a poll brings events; the live run's segments move on every SSE
 *  frame, many times a second. Deriving both from one function meant every
 *  frame rebuilt the whole committed transcript — two Maps and a `msgFromEvent`
 *  for every message in the thread — to append one token to the end of it.
 *
 *  So the expensive half is computed from inputs that a frame does not touch
 *  (`byId`, `order`, `idBySeq`, and the active run's ID but NOT its segments),
 *  and `composeTranscript` bolts the cheap, live half on. useThreadLog is where
 *  the two memo boundaries are drawn; the arrangement itself stays here, which
 *  is the rule this module exists to hold. */
export function selectCommitted(state: ThreadState): CommittedTranscript {
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

  return {
    blocks: out,
    heldForActiveRun: activeRunId ? held.get(activeRunId) || [] : [],
  };
}

/** Where a pending send sits in the committed blocks: immediately after the last
 *  block the log had when that send was made. */
function anchorIndex(blocks: TranscriptBlock[], afterSeq: number): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b.kind === "message" && b.seq <= afterSeq) return i + 1;
  }
  return 0;
}

/** The committed blocks, then the live run, then pending sends.
 *
 *  Cheap by construction: one array copy of the committed block REFERENCES
 *  (nothing is re-derived), plus work proportional to the live run's own
 *  segments and the handful of pending sends. This is what runs per SSE frame.
 *
 *  A PENDING SEND THE LOG HAS MOVED PAST GOES WHERE IT WAS MADE.
 *
 *  Every pending send used to be appended here, unconditionally, which is right
 *  for the ordinary one: it is the newest thing in the chat and belongs last.
 *  It is wrong the moment a send FAILS, because a failed send never leaves
 *  `pending` — nothing commits it and nothing retracts it — while the log goes
 *  on growing underneath. So the failed bubble stayed nailed to the bottom of
 *  the transcript and every message sent afterwards was drawn above it, along
 *  with every reply to those. Reported 2026-08-11, in those words. Anchoring on
 *  `afterSeq` puts it back among the messages it was actually typed between, and
 *  costs the common case nothing: a send in flight was made at the tail, so its
 *  anchor IS the end. */
export function composeTranscript(
  committed: CommittedTranscript,
  state: ThreadState,
): TranscriptBlock[] {
  const out = committed.blocks.slice();
  const tail = lastSeq(state);
  const pending = pendingSends(state);

  const toBlock = (p: PendingSend): TranscriptBlock => ({
    kind: "pending", key: `p:${p.clientId}`, clientId: p.clientId, text: p.text,
    status: p.status, ...(p.error ? { error: p.error } : {}),
    ...(p.attachments?.length ? { attachments: p.attachments } : {}),
  });

  // Anchored back-to-front so an index computed against `out` is still valid
  // when its turn comes: `pendingSends` is in send order and the log only grows,
  // so walking it backwards gives non-increasing indices, and two sends landing
  // on the same index keep their order (the later one is inserted first and the
  // earlier one pushes it right).
  const atTail: TranscriptBlock[] = [];
  for (let i = pending.length - 1; i >= 0; i--) {
    const p = pending[i];
    if (p.afterSeq >= tail) atTail.unshift(toBlock(p));
    else out.splice(anchorIndex(out, p.afterSeq), 0, toBlock(p));
  }

  if (state.activeRun) {
    out.push(...liveBlocks(
      state.activeRun.runId, state.activeRun.segments, committed.heldForActiveRun,
    ));
  }
  out.push(...atTail);

  return out;
}

/** A durable-queue entry the server already refused, from a PAST attempt this
 *  tab no longer remembers as pending (the reducer's copy does not survive a
 *  reload — see useSendPipeline's queue.pushFailed).
 *
 *  It is stale, not upcoming, so it must not land where `selectTranscript`
 *  puts genuinely-queued text: after the live run. A failure from before
 *  "now" sinking below a turn that started after it read as the chat ignoring
 *  whatever came next — reported 2026-08-10 ("it's BELOW my more recent
 *  message and below the typing indicator"). It goes immediately before
 *  whatever is happening live instead — the newest point it can truthfully
 *  sit at without claiming to be part of what is running now. */
/** The client ids the LOG already carries.
 *
 *  A send that fails is written to the durable queue so its text survives a
 *  reload (useMessageQueue invariant 1). A retry then reuses THE SAME client id
 *  on purpose, so if it lands, the server dedupes and the message is committed
 *  under the id the durable entry is still holding. From then on the durable
 *  entry is a GHOST: the message it stands for is in the transcript.
 *
 *  Nothing used to notice. `reconcilePending` drops the in-memory optimistic
 *  copy when the log carries its id, but the durable copy was only ever
 *  filtered against that same in-memory map — so the moment reconciliation
 *  removed the pending entry, the durable one started rendering and the message
 *  drew TWICE: once committed, once as a failed bubble with a Retry under it.
 *  The ghost appeared *because* the send had succeeded, and a reload could not
 *  clear it (localStorage holds it, and the message is permanently in the log).
 *  Reported 2026-08-11: "it printed it once and then it printed it again with
 *  the error message on the second one." */
export function committedClientIds(state: ThreadState): Set<string> {
  const ids = new Set<string>();
  for (const ev of committedEvents(state)) {
    if (ev.kind === "message" && ev.client_id) ids.add(ev.client_id);
  }
  return ids;
}

/** Drop queue entries the log already carries. See `committedClientIds`. */
export function withoutCommitted<T extends { clientId: string }>(
  items: T[], committed: Set<string>,
): T[] {
  if (!committed.size) return items;
  return items.some((i) => committed.has(i.clientId))
    ? items.filter((i) => !committed.has(i.clientId))
    : items;
}

export function withStaleFailures(
  blocks: TranscriptBlock[],
  staleFailed: {
    clientId: string; text: string; error?: string;
    // A send that failed and was reloaded still shows the files it carried, so
    // the retry visibly re-sends the same message rather than a bare line.
    attachments?: PendingSend["attachments"];
  }[],
): TranscriptBlock[] {
  if (!staleFailed.length) return blocks;
  const liveAt = blocks.findIndex((b) => b.kind === "live");
  const insertAt = liveAt === -1 ? blocks.length : liveAt;
  const extra: TranscriptBlock[] = staleFailed.map((q) => ({
    kind: "pending", key: `stale:${q.clientId}`, clientId: q.clientId,
    text: q.text, status: "failed",
    ...(q.attachments?.length ? { attachments: q.attachments } : {}),
    // WHY IT FAILED, carried across the reload with the text. It used to be
    // dropped here, so a 403 or a 500 came back after a refresh reading "this
    // message never reached the server" (UnsentNote's default) — the one
    // sentence that is true of no diagnosed failure at all.
    ...(q.error ? { error: q.error } : {}),
  }));
  return [...blocks.slice(0, insertAt), ...extra, ...blocks.slice(insertAt)];
}

/** Every committed message, flat, in log order. The context meter's local
 *  estimate and the "was this the first exchange" check read this; nothing
 *  renders from it. */
export function committedMessages(state: ThreadState): ChatMsg[] {
  return committedEvents(state)
    .filter((ev) => ev.kind === "message")
    .map(msgFromEvent);
}
