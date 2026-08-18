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
// WHERE THE CUT IS, IS RECORDED. NOT DERIVED. The server writes the message's
// cut token onto the segment it arrived after (`seg.cut_after`), or onto the
// reply's `cut_head` when the turn had emitted nothing yet, and it does so in
// the same call that seals the open text so the anchor cannot move between the
// two. The client matches tokens. There is no index to resolve, nothing to
// clamp, and nothing that moves when a segment elsewhere in the turn grows.
//
// That last clause is the whole reason it changed. The cut used to be
// `meta.at_unit`, an index into the reply's stream of rendered units, resolved
// here against a segment list that had grown since. With two delegations
// running at once, the first one gaining steps slid the index into it, snapped
// it to that delegation's end, and dropped the SECOND delegation below a
// message typed after it was launched. Measured 2026-08-12; the fixture is in
// transcriptBlocks.test.ts. `legacyPieces` still resolves the index, for the
// transcripts already on disk and for nothing else.
//
// A RUN ALREADY IN FLIGHT IS ITSELF SOMETHING THAT HAPPENED FIRST. The cut says
// where a message sits among the work; it does not license the message to sit
// above the run's own start. A turn announced itself before the operator typed,
// so the announcement stays above what he typed even when the turn has emitted
// nothing else yet. `workingPieceIndex` is the whole of that rule, and it is now
// the ONLY placement rule here that is not "render them in recorded order".
//
// See docs/decisions/0018.
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
  // `id` is the LOG EVENT's own id, which `key` is not: a reply cut open by the
  // messages that interrupted it produces several blocks keyed `${id}:${i}` and
  // they all came from the one event. Anything that has to name this message to
  // the server (branching from it) uses `id`; anything that has to tell two
  // rendered blocks apart uses `key`.
  //
  // `awaiting` marks a message the RUNNING TURN HAS NOT TAKEN UP YET: it is in
  // the log, it was handed to the turn, and the turn has emitted nothing since.
  // Set only inside a live run, and it clears itself the moment work lands
  // below. See `liveBlocks`.
  | { kind: "message"; key: string; id: string; seq: number; msg: ChatMsg; awaiting?: boolean }
  // The turn in flight. `working` marks the block the indicator belongs on —
  // the last one, because that is where the work is still happening.
  | { kind: "live"; key: string; turnId: string; segments: ChatSegment[]; working: boolean }
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
  // A context boundary. `clear` dropped what is behind it; `compact` replaced
  // it with a summary. Both are read from the log's own `boundary` event, and
  // both leave the transcript itself untouched: the cut is about what the MODEL
  // is handed, never about what the operator can see.
  | { kind: "cut"; key: string; count: number; mode: "clear" | "compact" }
  // This chat was re-bound to another scope, read from the log's own `scope`
  // event. It is where it happened, it survives a reload, and it is the same on
  // every device — none of which was true when it was React state.
  | { kind: "scope"; key: string; to: string };

/** One stretch of a reply, or one message that ended a stretch.
 *
 *  The list ALWAYS starts and ends with a `work` piece, so "the stretch still
 *  being written" and "the stretch before the first message" are always
 *  addressable even when empty. */
type Piece = { work: ChatSegment[] } | { msg: LogEvent };

const isWork = (p: Piece): p is { work: ChatSegment[] } => "work" in p;

/** Split a reply at the positions the SERVER RECORDED while it was writing.
 *
 *  Each message carries a cut token (`meta.cut_id`) and the server wrote that
 *  same token onto the segment the message arrived after (`seg.cut_after`), or
 *  onto the reply's `cut_head` when nothing had been emitted yet. Matching two
 *  tokens is exact. There is no index to resolve, nothing to clamp, and nothing
 *  that can move when a segment elsewhere in the turn grows.
 *
 *  A message whose token is nowhere in the reply goes last. That is the honest
 *  answer to "we do not know where this landed", and it is what the old
 *  coordinate did too when a message carried none.
 *
 *  Falls back to the unit index for replies recorded before 2026-08-12, which
 *  have no tokens. See `legacyPieces`, and docs/decisions/0018. */
function cutPieces(
  segments: ChatSegment[], interrupts: LogEvent[], cutHead?: string[],
): Piece[] {
  const byToken = new Map<string, LogEvent>();
  for (const m of interrupts) {
    const id = m.meta?.cut_id;
    if (id) byToken.set(id, m);
  }
  const recorded = !!cutHead?.length || segments.some((s) => s.cut_after?.length);
  // A LIVE RUN REACHES THE LEGACY PATH ONLY IF ITS MARKER IS MISSING, and for a
  // day and a half every live run did, because the frame carrying it had no
  // listener (see streamWire.ts). That is the drift the operator kept seeing:
  // an index resolved against a segment list still growing under it. The frame
  // is subscribed now, so this is the fallback it was always meant to be.
  if (!recorded || !byToken.size) return legacyPieces(segments, interrupts);

  const out: Piece[] = [];
  const placed = new Set<LogEvent>();
  let work: ChatSegment[] = [];
  const flush = () => { out.push({ work }); work = []; };
  const emit = (refs: readonly string[]) => {
    for (const r of refs) {
      const m = byToken.get(r);
      if (m && !placed.has(m)) { placed.add(m); out.push({ msg: m }); }
    }
  };

  // The head, and ONLY when something is there: an unconditional empty stretch
  // would put two `work` pieces next to each other with no message between.
  if (cutHead?.some((r) => byToken.has(r))) {
    flush();
    emit(cutHead);
  }
  for (const seg of segments) {
    work.push(seg);
    const refs = seg.cut_after?.filter((r) => {
      const m = byToken.get(r);
      return !!m && !placed.has(m);
    });
    if (refs?.length) { flush(); emit(refs); }
  }
  flush();

  const stray = interrupts.filter((m) => !placed.has(m));
  if (stray.length) {
    for (const m of stray) out.push({ msg: m });
    // Re-open a stretch after them, so a live run's indicator still has a place
    // BELOW a message it could not place. See workingPiece.
    out.push({ work: [] });
  }
  return out;
}

/** THE LEGACY PATH, for replies recorded before the position was written down.
 *
 *  Resolves `at_unit` against the final segment list. Everything wrong with
 *  that is in docs/decisions/0018; it is kept because those transcripts are on
 *  disk and must not shift under the operator, and it is quarantined here with
 *  one caller so it can be deleted outright when they age out. */
function legacyPieces(segments: ChatSegment[], interrupts: LogEvent[]): Piece[] {
  const parts = splitAtUnits(segments, interrupts.map((m) => cutPointOf(m.meta, segments)));
  const out: Piece[] = [];
  for (let i = 0; i < parts.length; i++) {
    out.push({ work: parts[i] });
    if (i < interrupts.length) out.push({ msg: interrupts[i] });
  }
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
    return [{ kind: "message", key: ev.id, id: ev.id, seq: ev.seq, msg }];
  }
  const segments = msg.meta?.segments || [];
  const pieces = cutPieces(segments, interrupts, msg.meta?.cut_head);
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
  pieces.forEach((p, i) => { if (isWork(p) && p.work.length) lastPart = i; });
  for (let i = 0; i < pieces.length; i++) {
    const piece = pieces[i];
    if (!isWork(piece)) {
      const m = piece.msg;
      out.push({ kind: "message", key: m.id, id: m.id, seq: m.seq, msg: msgFromEvent(m) });
      continue;
    }
    {
      const part = piece.work;
      if (!part.length) continue;
      // Only the final block carries how the TURN ended (its notes, its timeout
      // or interrupted tag, when it finished). Those describe the end of the
      // run, not the end of every stretch of work inside it.
      const tail = i === lastPart;
      const meta = { ...msg.meta, segments: part };
      // A cut position is a fact about the WHOLE reply, and this block is one
      // stretch of it. Left on, every stretch would claim the head messages.
      delete meta.cut_head;
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
        id: ev.id,
        seq: ev.seq,
        msg: { ...msg, content: textOf(part), meta },
      });
      emitted++;
    }
  }
  if (!emitted) {
    // Nothing had streamed when the messages landed, so every slice is empty and
    // the whole reply came after all of them. Say so by putting it last.
    out.push({ kind: "message", key: `${ev.id}:0`, id: ev.id, seq: ev.seq, msg });
  }
  return out;
}

/** Which slice of a live run carries the working indicator.
 *
 *  A RUN THAT WAS ALREADY IN FLIGHT RENDERS ABOVE THE MESSAGE THAT LANDED
 *  DURING IT. That is the whole invariant, and the indicator is the only thing
 *  that can hold it up when the run has produced nothing yet.
 *
 *  The indicator marks where the work is happening NOW, so it belongs on the
 *  LAST slice: work emitted after a message was produced by a turn that had
 *  been handed it. But when the run has emitted nothing at all, every slice is
 *  empty, the last one renders as nothing, and the message floats to the top of
 *  the run — above the "Working" line that had been on screen before it was
 *  typed. Which is the exact reading transcriptBlocks exists to prevent, and it
 *  is what the operator reported on 2026-08-12: "i see 'working...', i send
 *  another message, and my new message shows ABOVE it. if it was already
 *  working my new message could not be BEFORE it since it wouldn't have seen
 *  it."
 *
 *  So: the last slice, unless the run has emitted nothing, in which case slice
 *  zero — the run's own start, which precedes every message sent during it.
 *  Nothing else can express "this turn was already running when you typed". */
function workingPieceIndex(pieces: Piece[]): number {
  const work = pieces.map((p, i) => (isWork(p) ? i : -1)).filter((i) => i >= 0);
  const any = pieces.some((p) => isWork(p) && p.work.length);
  return any ? work[work.length - 1] : work[0];
}

/** The run in flight, cut the same way. One slice carries the working
 *  indicator, and it is emitted even when empty, because "a turn is running and
 *  has produced nothing yet" still has to be visible.
 *
 *  HAS THE TURN TAKEN THIS MESSAGE UP YET, and the answer is the transcript's
 *  own shape: work below the message means the turn was writing after it was
 *  handed it, and nothing below means it has not got to it. A message sent into
 *  a running turn is not read the moment it commits — it is written to the
 *  turn's stdin and sits there until the model reaches its next step, which on a
 *  long tool call is minutes. Committed and read are two different events and
 *  the bubble looked identical for both, so a message that had genuinely not
 *  been picked up was indistinguishable from one that had been answered.
 *  Reported 2026-08-16: "my latest message just sits unread by system".
 *
 *  This is derived, never stored, and it is the same fact the reader is already
 *  using: `awaiting` only dims the bubble that has nothing under it. The moment
 *  the turn emits its next segment the flag clears and the work renders below,
 *  which is the standard contract and needs no second surface to announce it. */
function liveBlocks(
  turnId: string, segments: ChatSegment[], interrupts: LogEvent[],
  cutHead?: string[],
): TranscriptBlock[] {
  const pieces = cutPieces(segments, interrupts, cutHead);
  const out: TranscriptBlock[] = [];
  const workingAt = workingPieceIndex(pieces);
  // The last stretch that actually carries work. A message after it has nothing
  // below it; a message before it has been overtaken by the turn.
  let lastWorkAt = -1;
  pieces.forEach((p, i) => { if (isWork(p) && p.work.length) lastWorkAt = i; });
  for (let i = 0; i < pieces.length; i++) {
    const piece = pieces[i];
    if (!isWork(piece)) {
      const m = piece.msg;
      out.push({
        kind: "message", key: m.id, id: m.id, seq: m.seq, msg: msgFromEvent(m),
        ...(i > lastWorkAt ? { awaiting: true } : {}),
      });
      continue;
    }
    if (piece.work.length || i === workingAt) {
      out.push({
        kind: "live",
        key: `${turnId}:${i}`,
        turnId,
        segments: piece.work,
        working: i === workingAt,
      });
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
  heldForActiveTurn: LogEvent[];
  /** The run that is GENUINELY still writing, which is not always the one the
   *  snapshot names. See `selectCommitted`. Null means render no live block. */
  liveTurnId: string | null;
};

/**
 * The whole transcript: committed log, the live run, then pending sends.
 *
 * `queued` is the type-ahead queue — messages the operator has hit enter on that
 * have not been sent at all yet. They belong at the very end, below everything
 * including the live turn, because that is the truthful place for something not
 * yet sent.
 */

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
  const activeTurnId = state.activeTurn?.turnId ?? null;

  // Which run each committed reply belongs to, and where it sits. A mid-run
  // message is held back only when the block it interrupted is still ahead of
  // it (or is the live run); otherwise it renders exactly where it is in the
  // log, which is what a message filed against an already-finished run wants.
  const replySeqByTurn = new Map<string, number>();
  for (const ev of events) {
    if (ev.kind !== "message" || ev.role !== "assistant") continue;
    const rid = ev.meta?.turn_id;
    if (rid && !replySeqByTurn.has(rid)) replySeqByTurn.set(rid, ev.seq);
  }

  // A RUN WHOSE REPLY IS ALREADY IN THE LOG IS FINISHED, whatever the snapshot
  // says. The two facts arrive on the same page and the server does not write
  // them atomically: chat_log.read_thread reads `meta` before the events, while
  // commit_run appends the reply first and clears `active_turn_id` after. A read
  // landing in that gap returns the committed reply AND `active_turn` still
  // naming the run that produced it, with its now-stale draft attached.
  //
  // Believing the snapshot there printed the previous reply twice: once
  // committed, and once again below the operator's new message as a block marked
  // `working`, which is why it looked like the answer was being written and then
  // "replaced" when the next sync cleared it. Reported 2026-08-12 in those words.
  const liveTurnId = activeTurnId && !replySeqByTurn.has(activeTurnId) ? activeTurnId : null;

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

  let messagesSoFar = 0;

  for (const ev of events) {
    if (ev.kind === "boundary") {
      // WHERE, from the log. WHETHER and WHICH KIND, from the server (below):
      // the log records every boundary ever written, and the server ignores
      // some of them. Drawing a divider for one the prompt does not honour is
      // the transcript describing something that is not happening.
      if (ev.mode !== "clear" && ev.mode !== "compact") continue;
      cutAt = out.length;

      continue;
    }
    if (ev.kind === "scope") {
      // Named by its scope KEY, resolved to a label by the renderer: the log
      // records what the chat was re-bound to, not what one build happened to
      // call it on screen.
      out.push({ kind: "scope", key: `scope:${ev.seq}`, to: ev.to || "" });
      continue;
    }
    if (ev.kind !== "message") continue;
    messagesSoFar++;
    if (ev.role === "user") {
      const rid = ev.meta?.during_turn;
      if (rid && (rid === liveTurnId || (replySeqByTurn.get(rid) ?? -1) > ev.seq)) {
        hold(rid, ev);
        continue;
      }
      out.push({ kind: "message", key: ev.id, id: ev.id, seq: ev.seq, msg: msgFromEvent(ev) });
      continue;
    }
    const rid = ev.meta?.turn_id;
    const interrupts = rid ? (held.get(rid) || []) : [];
    if (rid) held.delete(rid);
    out.push(...replyBlocks(ev, interrupts));
  }

  // The server's own answer, or nothing. `state.cut` is what it is APPLYING to
  // the prompt, so the count and the wording both come from there; the log only
  // says where in the order to draw it.
  if (cutAt >= 0 && state.cut) {
    out.splice(cutAt, 0, {
      kind: "cut", key: `cut:${state.cut.count}`,
      count: state.cut.count, mode: state.cut.mode,
    });
  }

  return {
    blocks: out,
    heldForActiveTurn: liveTurnId ? held.get(liveTurnId) || [] : [],
    liveTurnId,
  };
}

/** Where a pending send sits in the committed blocks: immediately after the last
 *  block the log had when that send was made. */
export function anchorIndex(blocks: TranscriptBlock[], afterSeq: number): number {
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

  // Only when the committed half agrees the run is still writing. See the
  // liveTurnId note in selectCommitted: the snapshot can name a run whose reply
  // is already committed above, and rendering its draft prints that reply twice.
  if (state.activeTurn && committed.liveTurnId === state.activeTurn.turnId) {
    out.push(...liveBlocks(
      state.activeTurn.turnId, state.activeTurn.segments, committed.heldForActiveTurn,
      state.activeTurn.cutHead,
    ));
  }
  out.push(...atTail);

  return out;
}

/** The client ids the LOG already carries.
 *
 *  A failed send is mirrored to durable storage so its text survives a reload
 *  (useSendOutbox). A retry then reuses THE SAME client id on purpose, so if it
 *  lands, the server dedupes and the message is committed under the id the
 *  durable entry is still holding. From then on that entry is a GHOST: the
 *  message it stands for is in the transcript.
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



/** Every committed message, flat, in log order. The context meter's local
 *  estimate and the "was this the first exchange" check read this; nothing
 *  renders from it. */
export function committedMessages(state: ThreadState): ChatMsg[] {
  return committedEvents(state)
    .filter((ev) => ev.kind === "message")
    .map(msgFromEvent);
}
