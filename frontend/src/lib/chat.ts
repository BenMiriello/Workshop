// Client for the generalized in-app chat backend (/api/chat/*).
// App-agnostic: every call is scoped by a `contextKey` (e.g. "budget").

// An assistant turn is an ordered list of segments so text and tool calls keep
// their interleaving (text → tool → text …) instead of collapsing into one
// block. Persisted on the message so a reload reconstructs the same turn.
export type ChatSegment =
  | { t: "text"; text: string }
  | { t: "tool"; name: string; input: string };

// Progress on something a message kicked off. `busy` is still-happening, and is
// what earns the animated dots.
export type MsgStatus = { text: string; busy?: boolean };

export type ChatMsg = {
  role: "user" | "assistant";
  content: string;
  ts: string;
  meta?: {
    notes?: string[];
    error?: boolean;
    // The turn hit its wall-clock limit and was stopped (vs a hard error) —
    // rendered as a calm "stopped, continue" note rather than a red error.
    timeout?: boolean;
    // Any OTHER non-completion ending. Nothing writes this today — the old
    // "max_steps" value is gone with the step cap — but the field stays so an
    // older transcript on disk still parses.
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
    // than queued behind it — so it reached the agent at the next boundary
    // between its actions.
    injected?: boolean;
    // A transient line UNDER this message reporting on something the message
    // itself set off and that is still going (today: /compact's server-side
    // fold). Client-only and never persisted: it describes right now, so a
    // reload should not resurrect it. It lives on the message, rather than in a
    // bar above the log, so it can never appear out of order relative to what
    // was said after it.
    status?: MsgStatus;
    // Ordered text/tool segments (assistant turns). Absent on plain messages.
    segments?: ChatSegment[];
    // True while the turn is still streaming; turn_id lets a reload reconnect
    // to the live SSE channel and keep rendering.
    streaming?: boolean;
    turn_id?: string;
    // The model that produced this assistant message — drives per-model display
    // handling (how aggressively to collapse thinking/tools, elision sizes).
    model?: string;
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
  message_count: number;
  last_message: string;
  model?: string | null;
  // How many leading messages the backend has folded into its running summary.
  // Raw messages are never dropped, so this is the only way a client can tell a
  // compacted thread from an uncompacted one — and the only way the fold marker
  // survives a reload.
  compacted_through?: number;
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

export type Thread = ThreadSummary & { messages: ChatMsg[] };

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

// Build a short, human error line from an HTTP failure. A gateway/CDN 5xx (e.g.
// a Cloudflare 502 while the origin restarts) returns a full HTML page as the
// body; never surface that — it renders as a giant red wall taking over the UI.
export function shortHttpError(status: number, statusText: string, body: string): string {
  const b = (body || "").trim();
  const looksHtml = b.startsWith("<") || b.toLowerCase().includes("<!doctype");
  if (status >= 502 && status <= 504) return `Server unavailable (${status}) — retrying…`;
  const detail = !looksHtml && b && b.length <= 200 ? b : "";
  return detail ? `${status} ${detail}` : (`${status} ${statusText || "request failed"}`).trim();
}

export type ProviderRole = "assistant" | "builder";

export type Provider = {
  key: string;
  label: string;
  role?: ProviderRole;
  models?: string[];
  default_model?: string;
  tools_active?: boolean;
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
  listThreads: (contextKey?: string) =>
    fetch(
      contextKey
        ? `/api/chat/threads?context=${encodeURIComponent(contextKey)}`
        : `/api/chat/threads`,
    ).then(j<ThreadSummary[]>),

  createThread: (contextKey: string, title = "") =>
    fetch(`/api/chat/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context_key: contextKey, title }),
    }).then(j<Thread>),

  getThread: (id: string) => fetch(`/api/chat/threads/${id}`).then(j<Thread>),

  // Re-key a thread onto a new context/provider (used by a ui-card's
  // Move-chat-into-project action). The thread id + history are unchanged; only
  // its context_key moves. Backend validates the key resolves to a real provider
  // (unknown -> 400). Same /api/chat/threads base path as the other calls.
  // Direct vs Orchestrated. Commits on change: cheap, reversible, and the mode
  // is re-stated to the model every turn, so a switch takes effect immediately
  // rather than only on a fresh session.
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

  deleteThread: (id: string) =>
    fetch(`/api/chat/threads/${id}`, { method: "DELETE" }).then(j<{ deleted: boolean }>),

  // Fold the STORED transcript into the running summary (half of /compact — the
  // half that shrinks what a fresh session re-reads). `live_session` reports
  // whether this chat also has a live claude session whose window only the CLI's
  // own /compact can reclaim; the caller sends that as a turn.
  compactThread: (id: string) =>
    fetch(`/api/chat/threads/${id}/compact`, { method: "POST" }).then(
      // Returns in milliseconds: `will_fold` is arithmetic, not the result of
      // the summarization call, which now runs detached server-side. Waiting for
      // it is what put ~30s between pressing enter and anything happening.
      j<{
        will_fold: number; kept: number; before: number;
        compacted_through: number; live_session: boolean;
      }>,
    ),

  // Record that an in-chat card has been acted on, so it stops offering an
  // action already taken. Fire-and-forget from the caller's perspective.
  setCardState: (id: string, key: string, state: CardState) =>
    fetch(`/api/chat/threads/${id}/card-state`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key, ...state }),
    }).then(j<{ card_states: Record<string, CardState> }>),

  // Hand a message to a turn that is ALREADY running. It lands at the next
  // boundary between the agent's actions rather than waiting out the whole
  // turn. `delivered: false` is not an error — the turn ended in the meantime
  // and the caller should queue the message as the next turn instead.
  injectIntoTurn: (turnId: string, content: string) =>
    fetch(`/api/chat/turns/${turnId}/inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(j<{ delivered: boolean; reason: string }>),

  // Same, addressed by CHAT rather than by turn. The client knows which
  // conversation it is in; the server resolves which turn is running on it.
  // Routing by turn id meant injection only worked in the component mount that
  // started the turn — a second tab, another device, or a re-attached turn had
  // no id to send and silently fell back to queueing.
  injectIntoThread: (threadId: string, content: string) =>
    fetch(`/api/chat/threads/${threadId}/inject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(j<{ delivered: boolean; turn_id: string | null; reason: string }>),

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
  previewSpawnTask: (id: string) =>
    fetch(`/api/chat/threads/${id}/spawn-task/preview`, { method: "POST" })
      .then(j<{ text: string; scope: string; runnable: boolean }>),

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
