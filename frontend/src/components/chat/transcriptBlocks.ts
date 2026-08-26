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
  // `inflight` is set only for a message handed to the RUNNING turn that the
  // turn has not visibly reached yet: it sits below the working indicator, so
  // no work has come after it. It renders dim, the same "not landed yet" state
  // as a pending send, and turns solid the moment work appears below it (the
  // indicator moves down and the message is now above it). See `liveBlocks`.
  // `optimistic` marks a block this browser is rendering from a still-pending
  // send, before the log carries it. It renders through the SAME MessageBubble
  // its committed form will, keyed identically (see `msgKey`), so the handover is
  // an in-place update rather than an unmount/remount. It carries no server id,
  // so it is excluded from branching (see MessageList).
  | { kind: "message"; key: string; id: string; seq: number; msg: ChatMsg; inflight?: boolean; optimistic?: boolean }
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
  //
  // `reset` is the third kind and it moves nothing: a compact that found
  // nothing worth folding (the tail was already short) still drops the live
  // session, and this is the honest record of that — no count, because none of
  // the prompt changed. Placed directly from the log's own event, unlike the
  // other two which wait on the server's resolved cut (see below): there is no
  // resolved state for a reset to wait on, since it never touches `cut_through`.
  //
  // `auto` is the fourth kind: the CLI compacted its own window mid-turn,
  // unasked (see chat_store.note_auto_compact). It never touches `cut_through`
  // either, so it is placed the same way `reset` is, directly from the log's
  // own event.
  | { kind: "cut"; key: string; count: number; mode: "clear" | "compact" | "reset" | "auto" }
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

/** ONE KEY PER MESSAGE, FOR ITS WHOLE LIFECYCLE.
 *
 *  A send this browser made carries a `client_id` from the optimistic bubble
 *  through to the committed event, so keying a user-message block on it (falling
 *  back to the event id for a message this tab did not send, which has none)
 *  makes the optimistic and committed forms THE SAME React element. The bubble
 *  updates in place instead of unmounting and remounting when the log catches up
 *  -- which, together with rendering both forms through MessageBubble, is what
 *  stops a just-sent message flashing and jumping as it changes hands from the
 *  optimistic path to the committed one. The one bug family this whole module
 *  keeps being reopened for. */
function msgKey(ev: { client_id?: string; id: string }): string {
  return ev.client_id || ev.id;
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
      out.push({ kind: "message", key: msgKey(m), id: m.id, seq: m.seq, msg: msgFromEvent(m) });
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
 *  A MESSAGE HANDED TO A RUNNING TURN DIMS ONLY WHILE IT IS BELOW THE WORKING
 *  INDICATOR. The read state and the indicator's position are the SAME fact, and
 *  that is the design: everything above the indicator has been reached, and a
 *  message below it has not. This reconciles two reports that looked opposed.
 *
 *  A blanket `awaiting` dim (added 2026-08-16 after "my latest message just sits
 *  unread by system") dimmed a message for the WHOLE time it waited, so it still
 *  read as unanswered once the turn had plainly moved past it, and was removed on
 *  2026-08-18: "show as active not waiting as soon as read." Removing it entirely
 *  then went too far the other way: a message typed under a working indicator,
 *  which the turn has NOT reached, rendered solid as though answered (reported
 *  2026-08-18, "shown as though read even though it's below a 'working' message").
 *
 *  The position already carries the answer. `workingAt` is the slice the
 *  indicator sits on; a message piece AFTER it has no work below it yet, so the
 *  turn has not reached it (dim). A message piece at or before it has work below
 *  it, which is the proof it was read (solid). When the run has emitted nothing,
 *  `workingAt` is slice zero and every message typed is below it, exactly right:
 *  the turn was already running and has reached none of them. The dim clears the
 *  instant a work slice appears below the message. */
function liveBlocks(
  turnId: string, segments: ChatSegment[], interrupts: LogEvent[],
  cutHead?: string[],
): TranscriptBlock[] {
  const pieces = cutPieces(segments, interrupts, cutHead);
  const out: TranscriptBlock[] = [];
  const workingAt = workingPieceIndex(pieces);
  for (let i = 0; i < pieces.length; i++) {
    const piece = pieces[i];
    if (!isWork(piece)) {
      const m = piece.msg;
      out.push({ kind: "message", key: msgKey(m), id: m.id, seq: m.seq, msg: msgFromEvent(m),
                 // Below the working slice = not reached yet = dim. See header.
                 inflight: i > workingAt });
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
      // A `reset` never moves `cut_through`, so there is no resolved state to
      // wait for and nothing for the server to disagree with: the event alone
      // is the whole answer, same as `scope` below. Pushed in place rather than
      // through `cutAt`/`state.cut`, which exist only to hold a real cut for a
      // resolved answer that a `reset` does not have.
      if (ev.mode === "reset") {
        out.push({ kind: "cut", key: `reset:${ev.seq}`, count: 0, mode: "reset" });
        continue;
      }
      // `auto` carries no cut position and no summary either, same as
      // `reset` above: pushed in place from the log's own event rather than
      // through `cutAt`/`state.cut`.
      if (ev.mode === "auto") {
        out.push({ kind: "cut", key: `auto:${ev.seq}`, count: 0, mode: "auto" });
        continue;
      }
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
      out.push({ kind: "message", key: msgKey(ev), id: ev.id, seq: ev.seq, msg: msgFromEvent(ev) });
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

/** Is the live run's own block actually on screen?
 *
 *  Only when the committed half agrees the run is still writing. See the
 *  liveTurnId note in selectCommitted: the snapshot can name a run whose reply
 *  is already committed above, and rendering its draft prints that reply twice.
 *
 *  Exported because it is not only an arrangement fact. `turnActive === true`
 *  does NOT imply an indicator is on screen, so every display-only derivation
 *  (the 1 Hz clock, the composer's streaming affordances) needs this same
 *  answer, and needs it to be THE same answer rather than a second copy of the
 *  rule. It is threaded out through useThreadLog into `deriveTurnLifecycle`. */
export function isLiveShown(committed: CommittedTranscript, state: ThreadState): boolean {
  const liveTurn = state.activeTurn;
  return !!(liveTurn && committed.liveTurnId === liveTurn.turnId);
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
  const liveTurn = state.activeTurn;
  const liveShown = isLiveShown(committed, state);

  // A FAILED send is the only one that renders as a `pending` block, because
  // PendingBubble carries its Retry. Every other optimistic send renders as a
  // `message` block through the SAME MessageBubble its committed form uses, keyed
  // by client_id (`msgKey`), so the log catching up is an in-place update, not an
  // unmount and remount. That single fact is what stops the flash and the jump
  // that five prior passes chased in the placement rules instead of here.
  const asPending = (p: PendingSend): TranscriptBlock => ({
    kind: "pending", key: `p:${p.clientId}`, clientId: p.clientId, text: p.text,
    status: p.status, ...(p.error ? { error: p.error } : {}),
    ...(p.attachments?.length ? { attachments: p.attachments } : {}),
  });
  const asMessage = (p: PendingSend, inflight?: boolean): TranscriptBlock => ({
    kind: "message", key: p.clientId, id: p.clientId, seq: tail + 1,
    optimistic: true,
    ...(inflight ? { inflight: true } : {}),
    msg: {
      role: "user", content: p.text, ts: p.ts,
      ...(p.attachments?.length ? { meta: { attachments: p.attachments } } : {}),
    },
  });
  const toBlock = (p: PendingSend): TranscriptBlock =>
    p.status === "failed" ? asPending(p) : asMessage(p);

  // THE CONVERSATION'S FIRST MESSAGE IS NEVER BELOW THE WORKING INDICATOR.
  // `duringActiveTurn` is captured from `!!state.activeTurn` at send time, and on
  // a NEW chat that flag can be stale (a turn left active on the thread this one
  // was opened from, or the new-chat lifecycle race): the first message is then
  // stamped as "sent during a turn" and drops below the indicator, exactly the
  // "it shows at the bottom then moves to the top once read" the operator has
  // reported repeatedly. But a turn cannot predate the message that started it,
  // so nothing the operator typed first can sit under a run that was already
  // going. When the committed log carries no message yet, a tail send IS that
  // first message and it starts the turn, so it goes above the indicator.
  const noCommittedMessage = !committed.blocks.some((b) => b.kind === "message");

  // Anchored sends: a FAILED bubble the log has since grown past. Walk
  // back-to-front so an index computed against `out` stays valid as earlier
  // inserts push later ones right (see anchorIndex).
  for (let i = pending.length - 1; i >= 0; i--) {
    const p = pending[i];
    if (p.afterSeq < tail) out.splice(anchorIndex(out, p.afterSeq), 0, toBlock(p));
  }

  // Tail sends split by whether they joined a turn already running. A mid-turn
  // send belongs inside the live run at the run's current tail; a fresh send (or
  // a failed one, or the conversation's first message) belongs above the whole
  // live block. Both are collected in send order.
  const midTurn: PendingSend[] = [];
  const beforeLive: TranscriptBlock[] = [];
  for (const p of pending) {
    if (p.afterSeq < tail) continue;                       // already anchored
    const joined = p.duringActiveTurn && !noCommittedMessage && liveShown
      && p.status !== "failed";
    if (joined) midTurn.push(p);
    else beforeLive.push(toBlock(p));
  }
  out.push(...beforeLive);

  if (liveTurn && liveShown) {
    // FOLD EACH NOT-YET-COMMITTED MID-TURN SEND INTO THE LIVE RUN AS AN INTERRUPT
    // CUT AT THE RUN'S CURRENT TAIL, then let `liveBlocks` lay the whole thing
    // out. This is the one placement that does not jump: the committed message
    // will flow through this SAME function with the SAME cut once the server
    // records it, so the optimistic and committed forms are block-for-block and
    // key-for-key identical (the message keyed by client_id via `msgKey`, the
    // work and the trailing indicator keyed by their piece index). Nothing
    // remounts and nothing relocates when the log catches up. A cut on the LAST
    // segment puts the send after all work streamed so far; if the run keeps
    // streaming, the real cut lands on an earlier segment and the message settles
    // UP by that much, never down and never to the bottom. When the run has
    // streamed nothing the cut goes in the head, which is what puts a message
    // that beat all output below the indicator and dim -- the read-state rule
    // `workingPieceIndex` enforces, the same as the committed `cut_head` form.
    //
    // The toggling this replaces (demote the trailing block's `working` flag only
    // when its own segment count was nonzero) is what produced the reported
    // top-then-bottom-then-top: that count changes between streamed frames, so
    // the message flipped sides frame to frame.
    let segs = liveTurn.segments;
    let head = liveTurn.cutHead ? [...liveTurn.cutHead] : [];
    const held = [...committed.heldForActiveTurn];
    for (const p of midTurn) {
      const cid = p.clientId;
      if (segs.length) {
        const last = segs[segs.length - 1];
        segs = [...segs.slice(0, -1),
                { ...last, cut_after: [...(last.cut_after || []), cid] }];
      } else {
        head = [...head, cid];
      }
      held.push({
        seq: tail + 1, id: cid, client_id: cid, ts: p.ts, kind: "message",
        role: "user", content: p.text,
        meta: { cut_id: cid, ...(p.attachments?.length ? { attachments: p.attachments } : {}) },
      });
    }
    const optimistic = new Set(midTurn.map((p) => p.clientId));
    const live = liveBlocks(liveTurn.turnId, segs, held, head);
    // Tag the folded-in sends so they are excluded from branching (no server id)
    // and can render their optimistic state; the committed form drops the tag.
    out.push(...live.map((b) =>
      b.kind === "message" && optimistic.has(b.key) ? { ...b, optimistic: true } : b));
  } else {
    // The run is not shown (its reply already committed above, or none): a
    // mid-turn send is just the newest message, at the end.
    out.push(...midTurn.map((p) => asMessage(p)));
  }

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
