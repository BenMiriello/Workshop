// Client for the generalized in-app chat backend (/api/chat/*).
// App-agnostic: every call is scoped by a `contextKey` (e.g. "budget").

// An assistant turn is an ordered list of segments so text and tool calls keep
// their interleaving (text → tool → text …) instead of collapsing into one
// block. Persisted on the message so a reload reconstructs the same turn.
export type ChatSegment =
  | { t: "text"; text: string }
  | { t: "tool"; name: string; input: string };

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
  title: string;
  updated_at: string;
  message_count: number;
  last_message: string;
  model?: string | null;
};

export type Thread = ThreadSummary & { messages: ChatMsg[] };

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
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

  deleteThread: (id: string) =>
    fetch(`/api/chat/threads/${id}`, { method: "DELETE" }).then(j<{ deleted: boolean }>),

  sendMessage: (id: string, content: string, model?: string) =>
    fetch(`/api/chat/threads/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(model ? { content, model } : { content }),
    }).then(j<{ message: ChatMsg; notes: string[] }>),

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
