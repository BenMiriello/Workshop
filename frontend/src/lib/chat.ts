// Client for the generalized in-app chat backend (/api/chat/*).
// App-agnostic: every call is scoped by a `contextKey` (e.g. "budget").

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
type CutAfter = { cut_after?: string[] };

export type ChatSegment =
  // `sealed` marks a text run that a mid-turn message closed. Streamed text
  // normally merges into the trailing text segment, so without this "A", a
  // message, then "B" would be one segment "AB" by the time the reply is cut,
  // and the message would render below text written after it. A sealed segment
  // never absorbs more text; the next character starts a new segment.
  | ({ t: "text"; text: string; sealed?: boolean } & CutAfter)
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
    // note and a stopped run were indistinguishable. `kind` is one of
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
    //   "operator"    — the operator pressed Stop. The run's process group was
    //                   signalled and whatever it had produced was committed.
    //   "interrupted" — a restart orphaned the run; the startup reconcile closed
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
    // Which RUN was in flight when this message arrived. The log files it above
    // that run's reply (the reply does not exist yet when the message lands),
    // and the transcript selector uses this to find the block it interrupted.
    during_run?: string;
    // The same fact under its old name, on messages recorded before the run
    // rebuild. Read nowhere in the client; kept so the type describes what is
    // actually on disk.
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
    // When the run that produced this reply began. `ts` is when it was
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
    // WHO PUT THIS HERE. True on a message the platform posted into the thread
    // with nobody watching: a finished job's run report, a question a sweep hit
    // and had to ask. It is not "the assistant replied to you", and the log has
    // to say so before the words are read, so these carry the autonomous agency
    // tier (rail + tint + label) instead of rendering as an ordinary reply.
    // Written by services/run_report.py and services/question_report.py.
    autonomous?: boolean;
    // What that background write WAS: "run_report" | "ticket_question". Carried
    // for the label; `autonomous` alone decides the treatment, so a new kind of
    // background post gets it right by setting that flag and nothing else.
    kind?: string;
  };
};

export type ThreadSummary = {
  id: string;
  context_key: string;
  // Entity-model 2026-07-18 §7 (C11 + P1). Derived from context_key for legacy
  // threads on read; persisted explicitly on new writes.
  scope?: string;
  scope_locked?: boolean;
  mode?: "orchestrated" | "direct";
  // Canonical form: `agent:<slug>`. Legacy agent:<slug> chats back-fill
  // to [that slug]; workshop/project chats start empty and grow when
  // participants are added (P4).
  participants?: string[];
  title: string;
  updated_at: string;
  // When this chat was archived. Absent on every live thread: the two lists are
  // disjoint, so a summary carrying this came from the archive.
  archived_at?: string | null;
  message_count: number;
  last_message: string;
  model?: string | null;
  // The effort level this chat runs at, persisted the same way `model` is.
  // Absent/null = the model's own default (no --effort is sent at all).
  effort?: string | null;
  // How much this chat asks before acting. Absent/null = the platform default
  // (bypassPermissions), which is what every chat ran at before the picker.
  permission_mode?: string | null;
  // How many leading messages /clear has taken out of the prompt. Raw messages
  // are never deleted, so this is the only way a client can tell a cut thread
  // from an uncut one, and the only way the divider survives a reload.
  //
  // There is only one kind of cut now. It used to also count messages folded
  // into a running summary, and `context_cut` existed to say which of the two
  // had happened; server-side folding was removed on 2026-08-12, so the
  // question no longer has two answers.
  cleared_through?: number;
  // Which in-chat cards have already been acted on, keyed by a digest of the
  // card block. Without this a card that was filed and run came back on the next
  // load looking exactly like one that had never been touched.
  card_states?: Record<string, CardState>;
};

export type CardState = {
  phase: string;              // "success" | "dismissed"
  note?: string | null;
  slug?: string | null;
  title?: string | null;
  ts?: string;
};

export type Thread = ThreadSummary & { messages: ChatMsg[] } & QueueState;

// ---- The event log (the wire shapes) ---------------------------------------
// A thread IS an append-only, sequence-numbered event log; everything else about
// it is a view. These are those events exactly as the server writes them. The
// client's model of a thread is built from nothing else — see
// components/chat/threadStore.

export type EventKind = "message" | "run" | "boundary" | "title" | "scope";

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
  // kind === "run" — a turn's lifecycle marker. The turn itself is not in the
  // log until it finishes.
  run_id?: string;
  state?: "started" | "finished" | "lost";
  // kind === "scope" — this chat was re-bound to another scope. In the log
  // because it is a thing that HAPPENED to the conversation, at a point in its
  // order. It was a client-side annotation once: visible only in the browser
  // that flipped the picker, gone on reload, absent on every other device.
  from?: string;
  to?: string;
  // kind === "boundary" — a context cut, which only /clear makes.
  // `mode` survives as a discriminator: a boundary left behind by the removed
  // folding path reads as "compact" and is ignored everywhere.
  mode?: "compact" | "clear";
  at_seq?: number;
  // kind === "title"
  title?: string;
};

// The run in flight. `live` is the server's throttled snapshot of what it has
// produced so far — the fallback for a run this browser is not streaming.
export type ActiveRunPayload = {
  run_id: string;
  started_seq?: number | null;
  // May a message be handed to this run while it is going? False for a slash
  // command (it folds or resets or reports and exits without reading a word) and
  // for a voice turn (driven by the voice worker, not by this module's input
  // channel). A message sent into either is a message nothing will ever answer,
  // so the client queues instead of injecting. Absent from an older backend,
  // which reads as "yes" and restores the old behaviour.
  accepts_input?: boolean;
  live?: {
    run_id?: string;
    content?: string;
    ts?: string;
    meta?: {
      segments?: ChatSegment[]; model?: string; agent?: string; turn_id?: string;
      // Messages that beat this run's first output. Carried on the live
      // snapshot as well as on the commit, because a run that has produced
      // nothing is exactly when it matters and is exactly when there are no
      // segments to carry it.
      cut_head?: string[];
    };
  } | null;
} | null;

/** One message the SERVER is holding to send next.
 *
 *  A chat runs one turn at a time and some turns read nothing at all (a slash
 *  command, a voice turn), so a message sent during one has to wait. It waits
 *  ON THE THREAD, not in this browser: the server records it, every device sees
 *  the same queue, and the server sends it when the run ends whether or not any
 *  browser is watching. See backend/services/chat_queue.py — this replaced a
 *  localStorage queue that lost two of the operator's messages on 2026-08-12. */
export type QueuedMessagePayload = {
  client_id: string;
  text: string;
  /** When the operator hit enter. */
  ts: string;
  attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
};

/** The context cut the server is APPLYING, and which command made it. */
export type CutState = {
  /** Messages no longer sent verbatim. 0 means no cut. */
  cleared_through?: number;
  /** `clear` dropped them; `compact` folded them into a summary. */
  cut_mode?: "clear" | "compact" | "";
};

/** The queue, as every read of a thread reports it. */
export type QueueState = {
  queued?: QueuedMessagePayload[];
  /** Stop is holding it. The operator's next send releases it. */
  queue_held?: boolean;
  /** Why it is not moving, when it stopped for a reason worth showing. */
  queue_error?: string;
};

/** One line of the conversation this chat could be on (ADR 0020).
 *
 *  A branch is a named pointer to a message. Going back to an earlier point
 *  creates one; nothing is deleted to do it, which is why the branch you left
 *  is still in this list. */
export type Branch = {
  id: string;
  name: string;
  ordinal: number;
  is_current: boolean;
  is_root: boolean;
  created_at: string;
  fork_message_id: string | null;
  /** A snippet of the message this branch carried on from. */
  forked_from: string | null;
  forked_from_role: string | null;
  forked_at: string | null;
  /** A snippet of the last thing said on it. */
  preview: string | null;
  last_at: string;
};

/** Which branch the server is serving, so the client can tell that it changed.
 *  Rides on the sync endpoint rather than its own, for the same reason the cut
 *  and the queue do: things that must agree may not arrive separately. */
export type BranchState = {
  branch: { id: string; name: string } | null;
  branch_count: number;
};

export type ThreadEventsPage = {
  events: LogEvent[];
  last_seq: number;
  active_run: ActiveRunPayload;
} & QueueState & CutState & BranchState;

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
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

// Build a short, human error line from an HTTP failure. A gateway/CDN 5xx (e.g.
// a Cloudflare 502 while the origin restarts) returns a full HTML page as the
// body; never surface that — it renders as a giant red wall taking over the UI.
export function shortHttpError(status: number, statusText: string, body: string): string {
  const b = unwrapDetail((body || "").trim());
  const looksHtml = b.startsWith("<") || b.toLowerCase().includes("<!doctype");
  if (status >= 502 && status <= 504) return `Server unavailable (${status}). Retrying.`;
  const detail = !looksHtml && b && b.length <= 200 ? b : "";
  return detail ? `${status} ${detail}` : (`${status} ${statusText || "request failed"}`).trim();
}

// FastAPI raises as `{"detail": "..."}`, so the sentence the backend wrote for
// the operator arrived wrapped in its own JSON and was shown that way. The
// refusal a chat gets for a developer-mode scope is a written sentence naming
// the flag; on screen it read `403 {"detail":"developer mode is off, ..."}`.
function unwrapDetail(body: string): string {
  if (!body.startsWith("{")) return body;
  try {
    const parsed = JSON.parse(body);
    const d = parsed?.detail;
    if (typeof d === "string" && d.trim()) return d.trim();
  } catch {
    /* not JSON, or not ours: the raw body is still the best thing to show */
  }
  return body;
}

export type ProviderRole = "assistant" | "builder";

// What a given model actually honours for a turn. Vendor-declared: the frontend
// renders the levels it is handed and never carries its own list, because the
// ladder differs per model (Sonnet 4.6 has `max` but no `xhigh`) and per vendor.
// An empty `levels` means no control at all, not a disabled one.
export type ModelCaps = {
  vendor: string;
  efforts: { levels: string[]; default: string | null };
};

// Operator-level chat settings (GET/POST /api/chat/prefs). `null` on either
// default means "no preference": the model's own effort, and the platform's
// permission mode (`platform_permission_mode`, shown so the settings screen can
// name what "no preference" resolves to instead of leaving it blank).
export type ChatPrefs = {
  default_mode: "direct" | "orchestrated";
  modes: string[];
  default_effort: string | null;
  efforts: string[];
  default_permission_mode: string | null;
  permission_modes: string[];
  platform_permission_mode: string;
};

// Write shape: every field optional, `""` clears one to "no preference".
export type ChatPrefsPatch = {
  default_mode?: "direct" | "orchestrated";
  default_effort?: string;
  default_permission_mode?: string;
};

export type Provider = {
  key: string;
  label: string;
  role?: ProviderRole;
  models?: string[];
  default_model?: string;
  tools_active?: boolean;
  model_caps?: Record<string, ModelCaps>;
  // Vendor-level: a permission mode is a property of the CLI running the turn,
  // not of the model answering, so it is not repeated per model.
  modes?: { options: string[]; default: string };
};

// ---- Per-agent task list (build agents only) -------------------------------
// Each build agent owns a task list at /api/agents/build/{slug}/tasks. The same
// store backs tasks the agent creates itself, so the UI just renders whatever
// the endpoint returns and refetches after mutations / after a turn completes.
export type AgentTaskStatus = "todo" | "doing" | "done" | "blocked";

export type AgentTask = {
  id: string;
  text: string;
  status: AgentTaskStatus;
  owner: string;
  parent: string | null;
  created: string;
  updated: string;
};

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
  createThread: (contextKey: string, title = "", mode?: "direct" | "orchestrated") =>
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
  // It also carries `messages`, and nothing in the client reads them any more.
  // The transcript comes from `threadEvents` and from nowhere else — a
  // transcript assembled by merging endpoints with local state is the
  // anti-pattern behind every reorder and eaten-message bug this chat has had.
  getThread: (id: string) => fetch(`/api/chat/threads/${id}`).then(j<Thread>),

  // THE SYNC ENDPOINT. The thread's event log from a cursor, plus the run in
  // flight. Called on mount, on focus, on reconnect, and after any write; the
  // client merges it with nothing except the live SSE for the active run.
  threadEvents: (id: string, after = 0) =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/events?after=${after}`)
      .then(j<ThreadEventsPage>),

  // Every line this conversation has taken. Empty when the relational store is
  // not reachable, which is a chat that cannot be branched rather than an error.
  listBranches: (id: string) =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/branches`)
      .then(j<{ branches: Branch[] }>),

  // Carry on from `anchor` (a message id from the transcript). Nothing is
  // deleted: the line being left keeps its own ref and stays in listBranches.
  forkThread: (id: string, anchor: string) =>
    fetch(`/api/chat/threads/${encodeURIComponent(id)}/branches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ anchor }),
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
  // Direct vs Orchestrated. Commits on change: cheap, reversible, and the mode
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

  setThreadMode: (id: string, mode: "direct" | "orchestrated") =>
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

  // /compact: drop the LIVE session so the next turn starts a fresh one from the
  // stored transcript. Nothing is folded, summarized or forgotten, and the cut
  // does not move; the CLI's private copy of the conversation is simply
  // discarded, and it is re-derivable from the transcript by definition.
  // `session_reset` says whether there was a live window to reclaim.
  // Fold the conversation: summarize everything but the recent tail, move the
  // cut, drop the live session. `folded` is how many messages it stood in for
  // and `error` is why it did not, when it did not — a summarizer is a model
  // call and it can fail, and this used to answer 200 over a fold that never
  // happened while the chat said "Compacted."
  compactThread: (id: string) =>
    fetch(`/api/chat/threads/${id}/compact`, { method: "POST" }).then(
      j<{
        folded: number;
        session_reset: boolean;
        error: string;
        summary_chars: number;
      }>,
    ),

  // Drop this thread's context outright — the other half of /clear vs /compact.
  // No summary, no live-session turn to send, no wait: this is a field write
  // and returns as soon as it lands.
  clearThreadContext: (id: string) =>
    fetch(`/api/chat/threads/${id}/clear`, { method: "POST" }).then(
      j<{ cleared: number; cleared_through: number; session_reset: boolean }>,
    ),

  // Record that an in-chat card has been acted on, so it stops offering an
  // action already taken. Fire-and-forget from the caller's perspective.
  setCardState: (id: string, key: string, state: CardState) =>
    fetch(`/api/chat/threads/${id}/card-state`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key, ...state }),
    }).then(j<{ card_states: Record<string, CardState> }>),

  // Hand a message to the run already going on this chat. Addressed by CHAT,
  // not by run id: the client knows which conversation it is in, and the server
  // resolves which run is live on it. Routing by run id meant injection only
  // worked in the component mount that started the run — a second tab, another
  // device, or a re-attached run had no id to send and silently fell back to
  // queueing, which is the whole thing this was supposed to end.
  //
  // The message is PERSISTED BEFORE DELIVERY is attempted, so `delivered: true`
  // means the chat has it whether or not the running turn took it. Only
  // `delivered: false` (no run was going) means the endpoint did not take the
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
      // queue and sends it when that run ends. The caller must NOT re-send it —
      // that is the difference from `delivered: false`, which means no run was
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
  // stops reporting a run in flight. Idempotent — stopping a turn that already
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

  // ---- Per-agent tasks (build agents only) ---------------------------------
  // `slug` is the part of a build contextKey after the "build:" prefix.
  listAgentTasks: (slug: string, includeDone = false) =>
    fetch(
      `/api/agents/build/${encodeURIComponent(slug)}/tasks?include_done=${includeDone ? "true" : "false"}`,
    )
      .then(j<{ tasks: AgentTask[] }>)
      .then((r) => r.tasks),

  addAgentTask: (slug: string, text: string, parent?: string) =>
    fetch(`/api/agents/build/${encodeURIComponent(slug)}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parent ? { text, parent } : { text }),
    }).then(j<AgentTask>),

  updateAgentTask: (
    slug: string,
    id: string,
    patch: { status?: AgentTaskStatus; text?: string },
  ) =>
    fetch(`/api/agents/build/${encodeURIComponent(slug)}/tasks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<AgentTask>),

  deleteAgentTask: (slug: string, id: string) =>
    fetch(`/api/agents/build/${encodeURIComponent(slug)}/tasks/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }).then(j<{ deleted: boolean }>),
};
