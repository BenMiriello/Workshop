// Client for the generalized in-app chat backend (/api/chat/*).
// App-agnostic: every call is scoped by a `contextKey` (e.g. "budget").

// The mode's VALUE SET is a wire fact and belongs to the server; its labels are
// a UI fact and belong to `chat/chatModes`. This module used to spell the union
// inline in five places, so changing the set of modes meant finding all five.
import type { ChatModeId } from "../components/chat/chatModes";

// An assistant turn is an ordered list of segments so text and tool calls keep
// their interleaving (text → tool → text …) instead of collapsing into one
// block. Persisted on the message so a reload reconstructs the same turn.
// One step of an assistant turn. `agent` is a subagent the turn delegated to:
// its own steps nest INSIDE it rather than flattening into the turn's work, and
// its interim text never joins the answer. The backend builds the same shape
// (build_streaming._segment_ingest) so a live turn and a reloaded one render
// identically.
/** A recorded cut position: the messages that arrived immediately AFTER this
 *  segment, by their cut token (`meta.cut_id`).
 *
 *  THE POSITION IS RECORDED, NOT DERIVED. It used to be an index into the
 *  reply's stream, carried on the message, and re-resolved at render time
 *  against a segment list that had grown since. An index cannot survive that:
 *  with two delegations running at once, the first one gaining steps slid the
 *  index into it, snapped it to that delegation's end, and dropped the SECOND
 *  delegation below a message typed after it was launched. "After this segment"
 *  cannot move. See docs/decisions/0018. */
// GENERATED, not hand-written. Every wire shape below is an alias into the
// backend's own OpenAPI schema (`npm run gen:api-types`), which knows them
// because `routes/chat.py` declares `response_model=` on each route and
// `routes/chat_models.py` describes what those routes actually return.
//
// WHY THIS FILE IN PARTICULAR. The hand-written versions had drifted in BOTH
// directions on the same type: `ThreadSummary` declared `card_states`,
// `effort`, `permission_mode` and `cleared_through`, none of which the LIST
// route returns (`chat_store._summary_row` emits twelve keys and not one of
// those is among them), while `Thread` was `ThreadSummary & QueueState` and so
// omitted most of what the DOCUMENT route does return. One name covered two
// different server shapes, so a field read off a list row was `undefined` at
// runtime while the compiler said it was there.
//
// The narrowings that remain below are deliberate and each says why. Anything
// not narrowed is the schema verbatim; do not re-widen or re-declare one here.
import type { components } from "../api/schema.gen";
import { HttpError, shortHttpError } from "@workshop/ui";

type CutAfter = { cut_after?: string[] };

export type ChatSegment =
  // `sealed` marks a text run that a mid-turn message closed. Streamed text
  // normally merges into the trailing text segment, so without this "A", a
  // message, then "B" would be one segment "AB" by the time the reply is cut,
  // and the message would render below text written after it. A sealed segment
  // never absorbs more text; the next character starts a new segment.
  | ({ t: "text"; text: string; sealed?: boolean } & CutAfter)
  // The model's REASONING, captured from the partial-message stream
  // (build_streaming --include-partial-messages). It is its own kind, never
  // `text`, so it is never part of the answer and never reaches AnswerBody: it
  // renders as a foldable "Thought" disclosure inside the work section, the same
  // channel ChatGPT and Claude fold reasoning into. `sealed` behaves as it does
  // for text (a mid-turn message closes the run). Coalesced like text while it
  // streams. Absent on every turn recorded before this shipped, which reads as a
  // turn that simply showed no reasoning.
  | ({ t: "thinking"; text: string; sealed?: boolean } & CutAfter)
  // A tool call, and how it went. The last four fields are what let a settled
  // turn distinguish its steps at all: before them every step rendered
  // identically, so a command that failed looked exactly like a file that was
  // read. All four are ABSENT on turns recorded before they existed, and on a
  // step whose turn ended before its result arrived, and neither case is a
  // failure. See components/chat/grouping.ts.
  | ({
      t: "tool";
      name: string;
      input: string;
      id?: string;             // the CLI's tool_use id; the result names it
      // When it started, epoch SECONDS (the backend's time.time()). Present
      // only while the call is open, so it doubles as "is this still running".
      at?: number;
      ok?: boolean;            // absent = outcome unknown, not failure
      ms?: number;             // how long it took, once it finished
      error?: string;          // the failure text, kept only when ok === false
    } & CutAfter)
  // The turn is BLOCKED, asking to use a tool. A segment rather than a transient
  // event because the process stays blocked across a page reload, and the reload
  // must not lose the only control that can unblock it.
  | ({
      t: "approval";
      id: string;            // the CLI's request_id; answering names it
      tool: string;          // "Write", "Bash", "ExitPlanMode"
      label: string;         // the CLI's own display name for it
      detail: string;        // one line naming the target ("hello.txt")
      input: Record<string, unknown>;
      // The CLI's own proposals, e.g. {type:"setMode",mode:"acceptEdits"} —
      // this is what "allow and stop asking" applies.
      suggestions: { type?: string; mode?: string; destination?: string }[];
      // stale = the turn ended without an answer, so answering can do nothing.
      state: "pending" | "allowed" | "denied" | "stale";
    } & CutAfter)
  | ({
      t: "agent";
      name: string;          // the subagent: a frontmatter name, or general-purpose
      // ONE LINE: the task, not the instructions. The collapsed chip shows this
      // and nothing else, clamped, never wrapping — a delegation is one row in
      // the parent's work, and a chip that unrolled a page of brief was the
      // loudest thing in a turn while saying the least about it.
      input: string;
      // The full brief the delegate was actually handed (backend caps it at
      // 8000 chars). This is what opening the chip is FOR. Absent on turns
      // recorded before the backend started sending it.
      detail?: string;
      id: string;            // the Agent tool_use id its steps point back at
      steps: ChatSegment[];  // its own tool calls and interim text
      done: boolean;
      // Why it ended, when it did not end by reporting back. Empty (or absent,
      // on turns recorded before the backend sent it) means it finished
      // normally. A delegate is killed with its parent, so a turn the operator
      // stopped and a turn lost to a restart both leave one of these behind.
      outcome?: "" | "stopped" | "lost" | "unfinished";
      result: string;        // what it reported back
    } & CutAfter);

// Progress on something a message kicked off. `busy` is still-happening, and is
// what earns the animated dots.
export type MsgStatus = { text: string; busy?: boolean };

/** A file the server has stored, as it comes back from the upload. */
export type StoredAttachment = {
  id: string;
  name: string;
  kind: "image" | "pdf" | "text";
  size: number;
  created_at?: string;
};

export type ChatMsg = {
  role: "user" | "assistant";
  content: string;
  ts: string;
  meta?: {
    // The action pipeline's plain results ("Saved a note to workshop"). Kept
    // because every stored turn has it; `receipts` is the same information
    // typed, and is what the transcript renders when it is there.
    notes?: string[];
    // WHAT THIS TURN CHANGED OUTSIDE THE CHAT: a note written, a ticket filed,
    // a preference captured. Not a step (steps fold away with the rest of a
    // turn's work) and not a status (that is how the turn ended). All three
    // used to render as the same anonymous `.chat-note` div, which is why a
    // note and a stopped turn were indistinguishable. `kind` is one of
    // note / ticket / learning / action / warn.
    // `ref` is what the write PRODUCED, so the receipt can be opened rather
    // than only read. Absent on a warning: a refusal or a parse failure made
    // nothing to link to.
    receipts?: {
      kind: string;
      label: string;
      ref?: { scope: string; id: string; title?: string };
    }[];
    error?: boolean;
    // FILES THE OPERATOR ATTACHED to this message. Recorded on the message that
    // carried them, so reopening the thread shows the same chips it showed when
    // it was sent rather than a bare line of text about files that are no longer
    // visible anywhere.
    attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
    // The turn hit its wall-clock limit and was stopped (vs a hard error) —
    // rendered as a calm "stopped, continue" note rather than a red error.
    timeout?: boolean;
    // Any OTHER non-completion ending, and WHICH one. Two values, because they
    // are two different events and must not read alike:
    //   "operator"    — the operator pressed Stop. The turn's process group was
    //                   signalled and whatever it had produced was committed.
    //   "interrupted" — a restart orphaned the turn; the startup reconcile closed
    //                   it out so a reply that will never come doesn't render as
    //                   either answered or a wordless bubble.
    // The wording for each lives in components/chat/stopReason.ts.
    stopped?: string;
    // The AGENT that produced this reply, when the turn was addressed to one by
    // @mention. Absent on ordinary assistant replies, which is what makes the
    // assistant the unmarked default and an agent reply visibly someone else's.
    agent?: string;
    // Client-only: this user message never reached the server, so it is NOT in
    // the transcript on disk. Marked rather than silently left looking sent —
    // an optimistic bubble that quietly isn't real is the "my messages keep
    // disappearing" bug.
    unsent?: boolean;
    // Sent while a turn was already running, and handed to that turn rather
    // than queued behind it.
    injected?: boolean;
    // Which TURN was in flight when this message arrived. The log files it
    // above that turn's reply (the reply does not exist yet when the message
    // lands), and the transcript selector uses this to find the block it
    // interrupted.
    during_turn?: string;
    // How much of the reply had streamed when the message arrived: the index
    // into the reply's segment stream at which it landed. THE CUT POINT. The
    // reply's block ends here and a new one begins below the message, so work
    // that had already been emitted stays above it and work produced after it
    // reads as having been produced after it. See transcriptBlocks.ts.
    at_segment?: number;
    // THE SAME CUT POINT, COUNTED PROPERLY, and the one the client reads when it
    // is there. `at_segment` counts only TOP-LEVEL segments, and a subagent's
    // steps nest inside one of those — so for the whole of a delegation the
    // coordinate does not move, and every message sent during it records the
    // same index and renders above work that had already happened. `at_unit`
    // counts a top-level segment OR a nested step, so it advances with the work.
    // Absent on messages recorded before 2026-08-10, which keep their original
    // placement via at_segment rather than shifting under the operator.
    at_unit?: number;
    // THE RECORDED POSITION, and the one the client reads when it is there.
    // Both numbers above are indexes re-resolved at render time; this is a
    // token the SERVER also wrote onto the segment the message arrived after
    // (`cut_after`), or into the reply's `cut_head` when the turn had emitted
    // nothing yet. Matching two tokens is exact; resolving an index is not.
    // Absent on every message recorded before 2026-08-12 and on the degraded
    // path where this process does not hold the live turn, both of which fall
    // back to at_unit. See docs/decisions/0018.
    cut_id?: string;
    // The browser's own id for this send. Present on messages this client sent;
    // it is what an optimistic bubble is reconciled against, never the text.
    client_id?: string;
    // When the turn that produced this reply began. `ts` is when it was
    // committed.
    started_at?: string;
    // Typed while a turn was running and sent as part of the NEXT turn, keeping
    // the time it was typed rather than the time the queue drained.
    queued?: boolean;
    // When the turn that produced this reply finished. `ts` is when it began; a
    // long turn without this claims to have happened at the instant it opened.
    ended_at?: string;
    // A transient line UNDER this message reporting on something the message
    // itself set off and that is still going (today: /compact's server-side
    // fold). Client-only and never persisted: it describes right now, so a
    // reload should not resurrect it. It lives on the message, rather than in a
    // bar above the log, so it can never appear out of order relative to what
    // was said after it.
    status?: MsgStatus;
    // Ordered text/tool segments (assistant turns). Absent on plain messages.
    segments?: ChatSegment[];
    // Messages that arrived before this turn emitted ANYTHING, by cut token.
    // The one cut position with no segment to sit on, named rather than left as
    // the index 0 (which renders as nothing, and floated the message above the
    // working indicator that was on screen before it was typed).
    cut_head?: string[];
    // True while the turn is still streaming; turn_id lets a reload reconnect
    // to the live SSE channel and keep rendering.
    streaming?: boolean;
    turn_id?: string;
    // The model that produced this assistant message — drives per-model display
    // handling (how aggressively to collapse thinking/tools, elision sizes).
    model?: string;
    // WHAT THIS MESSAGE WANTS FROM A PERSON. The axis the emphasis follows.
    //
    //   notify    FYI. Nothing is waiting on anyone.
    //   question  Uncertain, and asking rather than guessing.
    //   review    Proposes an action, for approve / edit / reject.
    //
    // Written by services/run_report.py, question_report.py and
    // background_runs.py. Taxonomy from agent-interaction-model-2026-07-31.md.
    //
    // THIS REPLACED `autonomous`, which was a boolean the producer set at write
    // time meaning "nobody was watching". Two things were wrong with it. It was
    // a GUESS about the room, not a fact about the message, so the operator
    // sitting in a thread when a report arrived read a label describing his own
    // absence and asked what the category was for. And it collapsed the one
    // message that needed him (a sweep's question) into the same treatment as
    // the ones that did not (a finished run's report), so the rail and the tint
    // marked provenance while the reader's actual question is "does this want
    // something from me". Whether anybody was attending is an observed fact,
    // read live, not a stored flag.
    asks?: "notify" | "question" | "review";
    // WHO SENT IT. "platform", or "run:<job id>". Rendered as the attribution
    // line on a notify. Anything that is not the operator is a sender like any
    // other; there is no separate tier for "the platform".
    sender?: string;
    // What this write WAS: "run_report" | "ticket_question" | "background_result".
    // Read for the sender's display name, and to place messages stored before
    // `asks` existed (see asksOf in MessageList).
    kind?: string;
    // WHICH ticket a `ticket_question` post is about. Written by
    // question_report.py since it shipped, and read by nothing until the inline
    // decision (`work/TicketDecision.tsx`) needed it: the message used to end in
    // a bare `/tickets/<scope>/<id>` path and expect the reader to follow it by
    // hand, which on a phone meant it could not be followed at all.
    ticket?: string;
    scope?: string;
  };
};

/** One sidebar row, exactly as `GET /threads` serves it.
 *
 *  This is the LIST shape and nothing more. It carries no `card_states`, no
 *  `effort`, no `permission_mode` and no `cleared_through`: those are settings,
 *  they live on the thread DOCUMENT, and the list has never sent them. See
 *  `Thread` below for the shape that does.
 *
 *  `scope` is derived server-side from `context_key` on every read and stored
 *  by nobody, so it cannot go stale after a rescope. `last_user_at` is when the
 *  OPERATOR last spoke, which is what the list orders by. */
export type ThreadSummary = components["schemas"]["ThreadSummary"];

/**
 * The agent this chat is with, or null.
 *
 * One derivation, in one place, because there are two ways a chat is an agent's
 * and they are not alternatives to choose between at each call site:
 *
 *  - `context_key: "agent:<slug>"` — the agent ANSWERS this chat. This is what
 *    "chat with an agent" creates, and it is the normal case.
 *  - `agent_id` — the chat was with an agent whose scope no longer exists (a
 *    retired build agent, one whose definition file is gone). Its transcript
 *    stays where its work is, and this is what still names who it was with.
 *
 * Used for the Agents filter, the list tag, and search, so all three agree.
 */
export function threadAgent(t: Pick<ThreadSummary, "context_key" | "agent_id">): string | null {
  const key = t.context_key || "";
  if (key.startsWith("agent:")) return key.slice("agent:".length) || null;
  return t.agent_id || null;
}

/** One in-chat card's recorded outcome, as STORED on the thread.
 *
 *  Not the write body: that is `{ key, phase, ... }`, and the `key` never comes
 *  back because it is what the state is filed under. `ts` is stamped by the
 *  server. The trailing index signature is the backend's `extra="allow"`: a
 *  card kind owns the shape of its own state, so the typed fields are a floor
 *  and not a ceiling. */
export type CardState = components["schemas"]["StoredCardState"];

/** A thread as SETTINGS, merged with its queue: what `GET /threads/{id}` and
 *  `POST /threads` serve. This is the DOCUMENT shape, and it is a different
 *  thing from `ThreadSummary` above -- it carries `effort`, `permission_mode`,
 *  `card_states`, `cleared_through`, `turn_marks` and the rest, and it does NOT
 *  carry the list's `message_count`, `last_message` or `last_user_at`.
 *
 *  Deliberately NOT `& { messages: ChatMsg[] }`: the server strips `messages`
 *  from this route's response (backend/routes/chat.py, thread()). The
 *  transcript comes from `threadEvents` only; see the comment on `getThread`
 *  below. Adding `messages` back here would let the compiler believe a field
 *  the server no longer sends. Measured 2026-08-14, the worst thread's
 *  `messages` was 4.77 MB of a 4.79 MB response no client read. */
export type Thread = components["schemas"]["ThreadWithQueue"];

// ---- The event log (the wire shapes) ---------------------------------------
// A thread IS an append-only, sequence-numbered event log; everything else about
// it is a view. These are those events exactly as the server writes them. The
// client's model of a thread is built from nothing else — see
// components/chat/threadStore.

export type EventKind = "message" | "turn" | "boundary" | "title" | "scope";

export type LogEvent = {
  // 1-based, assigned by the server, monotonic per thread, never reused. A
  // cursor, not an index: it survives anything the log does later.
  seq: number;
  id: string;
  ts: string;
  kind: EventKind;
  // kind === "message"
  role?: "user" | "assistant";
  content?: string;
  client_id?: string;
  meta?: ChatMsg["meta"];
  // kind === "turn" — a turn's lifecycle marker. The turn itself is not in the
  // log until it finishes.
  turn_id?: string;
  state?: "started" | "finished" | "lost";
  // kind === "scope" — this chat was re-bound to another scope. In the log
  // because it is a thing that HAPPENED to the conversation, at a point in its
  // order. It was a client-side annotation once: visible only in the browser
  // that flipped the picker, gone on reload, absent on every other device.
  from?: string;
  to?: string;
  // kind === "boundary" — a context cut (`clear`, `compact`), or a `reset`:
  // a compact that found nothing worth folding but still dropped the live
  // session. `auto` is a fourth kind: the CLI compacted its own window
  // mid-turn, unasked (see chat_store.note_auto_compact); rendered the same
  // as `compact` per the 2026-08-20 operator decision that auto and manual
  // are the same event with a different trigger. `mode` survives as a
  // discriminator: a boundary left behind by the removed folding path reads
  // as "compact" and is ignored everywhere.
  mode?: "compact" | "clear" | "reset" | "auto";
  at_seq?: number;
  // kind === "title"
  title?: string;
};

// The turn in flight. `live` is the server's throttled snapshot of what it has
// produced so far — the fallback for a turn this browser is not streaming.
/** The draft reply as it is being written. The backend leaves `live` as an open
 *  `dict` ON PURPOSE and says so in `routes/chat_models.py`: the renderer splits
 *  replies on positional fields (`meta.cut_head`, `seg.cut_after`) that a closed
 *  model would drop, and dropping exactly those is the 2026-08-14 regression
 *  that put the operator's message above half an hour of work. So the CLIENT
 *  narrows it here, since the client is what reads it, and the seam gets pinned
 *  by the contract test that has its own open ticket. */
export type LiveMessage = {
  turn_id?: string;
  content?: string;
  ts?: string;
  meta?: {
    segments?: ChatSegment[];
    model?: string;
    agent?: string;
    turn_id?: string;
    // Messages that beat this turn's first output. Carried on the live snapshot
    // as well as on the commit, because a turn that has produced nothing is
    // exactly when it matters and is exactly when there are no segments to
    // carry it.
    cut_head?: string[];
  };
};

/** The turn in flight, or `null` when none is. `accepts_input` is false for a
 *  slash command (it folds or resets or reports and exits without reading a
 *  word) and for a voice turn (driven by the voice worker, not by this module's
 *  input channel): a message sent into either is a message nothing will ever
 *  answer, so the client queues instead of injecting. */
export type ActiveTurnPayload =
  | (Omit<components["schemas"]["ActiveTurn"], "live"> & { live?: LiveMessage | null })
  | null;

/** One message the SERVER is holding to send next.
 *
 *  A chat runs one turn at a time and some turns read nothing at all (a slash
 *  command, a voice turn), so a message sent during one has to wait. It waits
 *  ON THE THREAD, not in this browser: the server records it, every device sees
 *  the same queue, and the server sends it when the turn ends whether or not any
 *  browser is watching. See backend/services/chat_queue.py — this replaced a
 *  localStorage queue that lost two of the operator's messages on 2026-08-12. */
export type QueuedMessagePayload = components["schemas"]["QueuedMessage"];

/** The context cut the server is APPLYING, and which command made it. */
/** The context cut the server is APPLYING, and which command made it.
 *  `cleared_through` is 0 when there is no cut. */
export type CutState = Pick<
  components["schemas"]["ThreadEvents"], "cleared_through" | "cut_mode">;

/** The queue, as every read of a thread reports it. */
export type QueueState = components["schemas"]["QueueState"];

/** One line of the conversation this chat could be on (ADR 0020).
 *
 *  A branch is a named pointer to a message. Going back to an earlier point
 *  creates one; nothing is deleted to do it, which is why the branch you left
 *  is still in this list. */
export type Branch = components["schemas"]["Branch"];

/** Which branch the server is serving, so the client can tell that it changed.
 *  Rides on the sync endpoint rather than its own, for the same reason the cut
 *  and the queue do: things that must agree may not arrive separately. */
/** Which branch the server is serving, so the client can tell that it changed.
 *  Rides on the sync endpoint rather than its own, for the same reason the cut
 *  and the queue do: things that must agree may not arrive separately. */
export type BranchState = Pick<
  components["schemas"]["ThreadEvents"], "branch" | "branch_count">;

/** THE sync endpoint's payload. Generated except for the two payloads the
 *  backend deliberately leaves open (`events`, and `live` inside `active_turn`)
 *  -- see `ActiveTurnPayload` above for why, and do not narrow them there. */
export type ThreadEventsPage =
  Omit<components["schemas"]["ThreadEvents"], "events" | "active_turn"> & {
    events: LogEvent[];
    active_turn: ActiveTurnPayload;
  };

// This browser's id for one send, stamped before the request leaves and reused
// verbatim on a retry.
//
// It is the ONLY thing an optimistic bubble is reconciled against. The client
// used to match `messages[messages.length - 1].content === text`, which popped
// the wrong bubble the moment the operator said the same thing twice and could
// not tell an optimistic bubble from a committed one at all. The log dedupes on
// this id, so re-sending after a dropped response cannot show the message twice.
export function newClientId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  // randomUUID needs a secure context; this app has one, but a non-unique id
  // would silently mean "the same message" to the server, so never risk it.
  return `cid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    // Typed so a caller can tell a restart-window 502 from a real refusal; the
    // message is still the shortHttpError line, so `${e}` is unchanged.
    throw new HttpError(r.status, shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return r.json() as Promise<T>;
}

// The shared HTTP-failure-to-error-line helper lives in ./httpError (a pure
// module with no chat dependency, so any page or lib can use it without
// pulling in this whole client). Re-exported here since existing callers
// import it from this module.
export { shortHttpError };

// What a given model actually honours for a turn. Vendor-declared: the frontend
// renders the levels it is handed and never carries its own list, because the
// ladder differs per model (Sonnet 4.6 has `max` but no `xhigh`) and per vendor.
// An empty `levels` means no control at all, not a disabled one.
export type ModelCaps = components["schemas"]["ModelCaps"];

// Operator-level chat settings (GET/POST /api/chat/prefs). `null` on either
// default means "no preference": the model's own effort, and the platform's
// permission mode (`platform_permission_mode`, shown so the settings screen can
// name what "no preference" resolves to instead of leaving it blank).
export type ChatPrefs = components["schemas"]["ChatPrefsOut"];

// Write shape: every field optional, `""` clears one to "no preference".
// Write shape: every field optional, `""` clears one back to "no preference".
// Named `ChatPrefs` on the backend -- the request model took the plain name and
// the response model is the one that had to be suffixed.
export type ChatPrefsPatch = components["schemas"]["ChatPrefs"];

export type Provider = components["schemas"]["Provider"];

export const chatApi = {
  // ONE FILE PER REQUEST, and the request happens on drop rather than on send.
  // A 12MB screenshot uploading inside the send would make Send feel broken for
  // as long as it took, with nothing to look at; uploading on drop means the
  // chip carries the progress and Send is instant. No Content-Type header: the
  // browser has to set the multipart boundary itself.
  uploadAttachment: async (
    file: File, opts?: { scope?: string; threadId?: string | null },
  ): Promise<StoredAttachment> => {
    const form = new FormData();
    form.append("file", file);
    if (opts?.scope) form.append("scope", opts.scope);
    if (opts?.threadId) form.append("thread_id", opts.threadId);
    const r = await fetch(`/api/chat/attachments`, { method: "POST", body: form });
    if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text()));
    return r.json();
  },

  /** Where the stored bytes are served: the chip's and the transcript's preview. */
  attachmentUrl: (id: string) => `/api/chat/attachments/${encodeURIComponent(id)}`,

  // With `scope` set to a contextKey the backend returns ONLY the relevant
  // provider(s) (used by an in-app dock); without scope it returns the FULL list
  // (used by the Agents page / focused chat so the operator can pick any).
  listProviders: (scope?: string) =>
    fetch(
      scope ? `/api/chat/providers?scope=${encodeURIComponent(scope)}` : `/api/chat/providers`,
    ).then(j<Provider[]>),

  // listThreads now accepts an empty contextKey to list ALL threads (used when
  // we want the cross-scope thread picker to surface every chat the operator
  // has ever opened, regardless of which app they're in now).
  // `archived` SWITCHES LISTS rather than widening one: the default is every
  // live thread, `true` is the archive and nothing else. Nothing needs both, and
  // a mixed list would need a marker per row to be readable.
  listThreads: (contextKey?: string, archived = false) => {
    const q = new URLSearchParams();
    if (contextKey) q.set("context", contextKey);
    if (archived) q.set("archived", "true");
    const qs = q.toString();
    return fetch(`/api/chat/threads${qs ? `?${qs}` : ""}`).then(j<ThreadSummary[]>);
  },

  // `mode` is sent only when the operator chose one on the blank chat before
  // sending; omitted, the server applies their default for new chats.
  createThread: (contextKey: string, title = "", mode?: ChatModeId) =>
    fetch(`/api/chat/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context_key: contextKey, title, ...(mode ? { mode } : {}) }),
    }).then(j<Thread>),

  // Operator-level chat settings.
  //
  // `default_mode` is baked into a chat AT CREATION, so changing it never
  // rewrites a conversation in flight. The other two are consulted per turn,
  // BELOW the thread's own stored value — so they move every chat that never
  // picked for itself, and no chat that did.
  //
  // The CHOICES are served, never assumed here: which permission modes a chat
  // may pick is the backend's decision (bypass is not among them) and the effort
  // ladder is the vendor's.
  getChatPrefs: () => fetch(`/api/chat/prefs`).then(j<ChatPrefs>),

  // Only the fields passed are written. `""` clears a default back to "no
  // preference"; omitting a field leaves it alone.
  setChatPrefs: (patch: ChatPrefsPatch) =>
    fetch(`/api/chat/prefs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<ChatPrefs>),

  // The thread's SETTINGS: title, model, mode, card states, scope. Called once
  // when a chat opens and after a write that changes one of them.
  //
  // It used to also carry `messages` (up to 4.77 MB on the worst thread) even
  // though nothing in the client read them; the server now strips that field
  // from this route entirely. The transcript comes from `threadEvents` and
  // from nowhere else — a transcript assembled by merging endpoints with
  // local state is the anti-pattern behind every reorder and eaten-message
  // bug this chat has had.
  getThread: (id: string) => fetch(`/api/chat/threads/${id}`).then(j<Thread>),

  // THE SYNC ENDPOINT. The thread's event log from a cursor, plus the turn in
  // flight. Called on mount, on focus, on reconnect, and after any write; the
  // client merges it with nothing except the live SSE for the active turn.
  //
  // `opts.tail`/`opts.before` page BACKWARD from the newest events instead of
  // reading from a cursor — see backend/routes/chat.py `thread_events`. They
  // are only honoured by the server when `after` is 0; useThreadLog uses them
  // for the first paint of a thread (and for paging further into its history),
  // never alongside a real cursor.
  threadEvents: (id: string, after = 0, opts?: { tail?: number; before?: number }) => {
    const q = new URLSearchParams({ after: String(after) });
    if (opts?.tail !== undefined) q.set("tail", String(opts.tail));
    if (opts?.before !== undefined) q.set("before", String(opts.before));
    return fetch(`/api/chat/threads/${encodeURIComponent(id)}/events?${q.toString()}`)
      .then(j<ThreadEventsPage>);
  },

  // Every line this conversation has taken. Empty when the relational store is
  // not reachable, which is a chat that cannot be branched rather than an error.
  listBranches: (id: string) =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/branches`)
      .then(j<{ branches: Branch[] }>),

  // Carry on from `anchor` (a message id from the transcript). Nothing is
  // deleted: the line being left keeps its own ref and stays in listBranches.
  // `mode` says whether the anchor is kept: "after" for a reply (keep this
  // answer, drop what followed), "before" for your own message (take it back).
  forkThread: (id: string, anchor: string, mode: "after" | "before" = "after") =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/branches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ anchor, mode }),
    }).then(j<{ branch: { id: string }; branches: Branch[] }>),

  // Show a different branch. One pointer moves; nothing else does.
  switchBranch: (id: string, branchId: string) =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/branches/${encodeURIComponent(branchId)}`,
          { method: "POST" })
      .then(j<{ branch: { id: string }; branches: Branch[] }>),

  // Re-key a thread onto a new context/provider (used by a ui-card's
  // Move-chat-into-project action). The thread id + history are unchanged; only
  // its context_key moves. Backend validates the key resolves to a real provider
  // (unknown -> 400). Same /api/chat/threads base path as the other calls.
  // Direct vs Auto. Commits on change: cheap, reversible, and the mode
  // is re-stated to the model every turn, so a switch takes effect immediately
  // rather than only on a fresh session.
  // Answer a tool-permission request from a running turn. The turn is blocked on
  // stdin until this lands and there is no wall clock on a chat turn, so nothing
  // else will ever unblock it. 409 means the request can no longer be answered
  // (the turn ended) — a real answer, not a transport failure.
  answerPermission: (
    threadId: string,
    turnId: string,
    body: { request_id: string; behavior: "allow" | "deny"; message?: string; apply_suggestion?: boolean },
  ) =>
    fetch(`/api/chat/threads/${threadId}/turns/${turnId}/permission`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<{ state: string; permission_mode: string | null }>),

  setThreadMode: (id: string, mode: ChatModeId) =>
    fetch(`/api/chat/threads/${id}/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }).then(j<{ id: string; mode: string }>),

  rescopeThread: (id: string, contextKey: string) =>
    fetch(`/api/chat/threads/${id}/rescope`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context_key: contextKey }),
    }).then(j<ThreadSummary>),

  // Closing a chat is archiving it. This is what the chat's own menu does, and
  // it is reversible, which is why it asks nothing before doing it.
  archiveThread: (id: string) =>
    fetch(`/api/chat/threads/${id}/archive`, { method: "POST" }).then(j<ThreadSummary>),

  restoreThread: (id: string) =>
    fetch(`/api/chat/threads/${id}/restore`, { method: "POST" }).then(j<ThreadSummary>),

  // The destructive one, reachable only from the archive: a chat is archived
  // first, then deleted from there, so this is never one tap away from a
  // conversation you are in.
  deleteThread: (id: string) =>
    fetch(`/api/chat/threads/${id}`, { method: "DELETE" }).then(j<{ deleted: boolean }>),

  // Overrides the auto-picked title. A chat names itself from its own content
  // once it has enough to go on — this is the operator saying "no, call it
  // this" instead.
  renameThread: (id: string, title: string) =>
    fetch(`/api/chat/threads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<ThreadSummary>),

  // The thread's own settings, partially. Only the keys sent change, so arming
  // a thread never touches its role and naming a role never arms it -- the two
  // fields are independent and the write shape says so. `role: null` clears it.
  patchThread: (id: string, patch: components["schemas"]["PatchThread"]) =>
    fetch(`/api/chat/threads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<ThreadSummary>),

  // /compact: run the CLI's OWN compaction on this chat's live session.
  //
  // It is a TURN, not a server-side edit of the transcript. Workshop used to
  // summarize the conversation itself and move a cut; that fold is gone, and
  // the provider's compaction is what runs. So this starts the turn and
  // returns it: the divider, the reply and the new context reading all arrive
  // on the change stream like any other turn's output.
  //
  // `queued` is an acceptance, not a refusal — a command cannot be spoken
  // into a running turn, so it waits in the thread's queue and is sent the
  // moment that turn ends.
  compactThread: (id: string) =>
    fetch(`/api/chat/threads/${id}/compact`, { method: "POST" }).then(
      j<{
        turn_id?: string;
        queued?: boolean;
        error?: string;
      }>,
    ),

  // Drop this thread's context outright — the other half of /clear vs /compact.
  // No summary, no live-session turn to send: a handful of field writes that
  // land when the thread is quiet.
  clearThreadContext: (id: string) =>
    fetch(`/api/chat/threads/${id}/clear`, { method: "POST" }).then(
      j<{ cleared: number; cleared_through: number; session_reset: boolean;
          deferred?: boolean; busy?: boolean; cancelled?: boolean }>,
    ),

  // Call off the /compact or /clear in flight. What the Stop control on the
  // fold line does: the server refuses the landing, so the transcript is
  // untouched and anything queued behind the operation goes out at once.
  // `cancelled: false` means it had already landed, which is a race, not a
  // failure.
  cancelThreadOperation: (id: string) =>
    fetch(`/api/chat/threads/${id}/operation/cancel`, { method: "POST" }).then(
      j<{ cancelled: boolean; operation: string }>,
    ),

  // Record that an in-chat card has been acted on, so it stops offering an
  // action already taken. Fire-and-forget from the caller's perspective.
  setCardState: (id: string, key: string, state: CardState) =>
    fetch(`/api/chat/threads/${id}/card-state`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key, ...state }),
    }).then(j<{ card_states: Record<string, CardState> }>),

  // Hand a message to the turn already going on this chat. Addressed by CHAT,
  // not by turn id: the client knows which conversation it is in, and the
  // server resolves which turn is live on it. Routing by turn id meant
  // injection only worked in the component mount that started the turn — a
  // second tab, another device, or a re-attached turn had no id to send and
  // silently fell back to queueing, which is the whole thing this was supposed
  // to end.
  //
  // The message is PERSISTED BEFORE DELIVERY is attempted, so `delivered: true`
  // means the chat has it whether or not the running turn took it. Only
  // `delivered: false` (no turn was going) means the endpoint did not take the
  // message and the caller must send it as a turn.
  injectIntoThread: (threadId: string, content: string, clientId?: string,
                     opts?: {
                       sentAt?: string;
                       attachments?: { id: string; name: string; kind: string }[];
                       draftKey?: string;
                     }) =>
    fetch(`/api/chat/threads/${threadId}/inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        ...(clientId ? { client_id: clientId } : {}),
        // A file attached to a message typed DURING a turn reaches the running
        // model the same way it would have at the start of one.
        ...(opts?.attachments?.length ? { attachments: opts.attachments } : {}),
        // When the operator hit enter, and which composer it came out of. Both
        // matter when the message does not run immediately: it is queued under
        // its own send time, and the server clears the draft it came from.
        ...(opts?.sentAt ? { sent_at: opts.sentAt } : {}),
        ...(opts?.draftKey ? { draft_key: opts.draftKey } : {}),
      }),
    }).then(j<{
      delivered: boolean; injected?: boolean; queued_for_turn?: boolean;
      // The chat is running something that reads no input (a slash command, a
      // voice turn). TAKEN AND DURABLE: the server recorded it on the thread's
      // queue and sends it when that turn ends. The caller must NOT re-send it —
      // that is the difference from `delivered: false`, which means no turn was
      // going and this message should become a turn right now.
      queued?: boolean;
      queue?: QueueState;
      // An OLDER backend's answer: it took nothing and expected the browser to
      // hold the message. This client refuses to, so a deferral is treated as a
      // failure rather than silently dropping the text. Kept in the type so that
      // refusal is deliberate rather than an untyped field nobody handles.
      deferred?: boolean;
      turn_id: string | null; at_segment?: number | null; reason: string;
    }>),

  // Take a queued message back out. It is the operator's text and unsending it
  // before it goes is theirs to do; this is the only way anything leaves the
  // queue undelivered.
  cancelQueued: (threadId: string, clientId: string) =>
    fetch(`/api/chat/threads/${threadId}/queued/${encodeURIComponent(clientId)}/cancel`,
          { method: "POST" }).then(j<QueueState>),

  // Send what is waiting: clears a Stop hold, or the diagnosis left by a drain
  // that failed. The Retry under a stuck queue and "go ahead" after a Stop are
  // the same action.
  releaseQueue: (threadId: string) =>
    fetch(`/api/chat/threads/${threadId}/queued/release`, { method: "POST" })
      .then(j<QueueState>),

  // Stop a running turn. Not "stop watching it": the run's process group is
  // signalled, whatever it produced is committed as the reply, and the thread
  // stops reporting a turn in flight. Idempotent — stopping a turn that already
  // finished answers `stopped: false` and is not an error, so a double-click and
  // a race with the turn's own ending both land somewhere sane.
  stopTurn: (turnId: string) =>
    fetch(`/api/chat/turns/${turnId}/stop`, { method: "POST" }).then(
      j<{ turn_id: string; stopped: boolean; committed?: boolean; reason: string }>,
    ),

  sendMessage: (id: string, content: string, model?: string) =>
    fetch(`/api/chat/threads/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(model ? { content, model } : { content }),
    }).then(j<{ message: ChatMsg; notes: string[] }>),

  // Turn this chat into a self-contained background task (Stage 4). Files a
  // ticket into a project's queue (the chat's scope, else the workshop queue),
  // distilling the conversation when no explicit text is given.
  // What this chat WOULD file, without filing it. Lets the operator read and
  // correct the derived text, and tells them up front whether the scope can
  // actually run it.
  spawnTask: (id: string, body?: { text?: string; project?: string; run?: boolean }) =>
    fetch(`/api/chat/threads/${id}/spawn-task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(
      j<{
        task: { id: string; text: string; status: string };
        scope: string;
        thread_id: string;
        decision?: string;
        message?: string;
        job_id?: string;
        run_error?: string;
      }>,
    ),

  // Real context-window usage for the composer meter: the backend assembles the
  // actual prompt (persona + live app context + windowed history) and counts it.
  threadContext: (id: string) =>
    fetch(`/api/chat/threads/${id}/context`).then(
      j<{ used_tokens: number; window_tokens: number; pct: number; model: string }>,
    ),

};
