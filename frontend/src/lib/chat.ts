// Client for the generalized in-app chat backend (/api/chat/*).
// App-agnostic: every call is scoped by a `contextKey` (e.g. "budget").

export type ChatMsg = {
  role: "user" | "assistant";
  content: string;
  ts: string;
  meta?: { notes?: string[]; error?: boolean };
};

export type ThreadSummary = {
  id: string;
  context_key: string;
  title: string;
  updated_at: string;
  message_count: number;
  last_message: string;
};

export type Thread = ThreadSummary & { messages: ChatMsg[] };

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export type Provider = { key: string; label: string };

export const chatApi = {
  listProviders: () => fetch(`/api/chat/providers`).then(j<Provider[]>),

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

  sendMessage: (id: string, content: string) =>
    fetch(`/api/chat/threads/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(j<{ message: ChatMsg; notes: string[] }>),
};
