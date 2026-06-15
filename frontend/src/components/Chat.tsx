import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Trash2, CornerDownLeft, Square, Wrench, Maximize2, ChevronLeft, WifiOff, ChevronRight, ChevronDown, ListTodo, X } from "lucide-react";
import { Button, ConfirmDialog, Select, StateBlock, Skeleton, Input, Toggle } from "../ui";
import {
  chatApi,
  type ChatMsg,
  type Thread,
  type ThreadSummary,
  type Provider,
  type AgentTask,
  type AgentTaskStatus,
} from "../lib/chat";

// Unified Chat component. Replaces ChatPanel + BuildChatPanel.
//
// mode = "data"  → POST /threads/{id}/messages (synchronous response)
// mode = "build" → POST /threads/{id}/turns + EventSource /turns/{id}/stream
//
// variant controls chrome:
//   "dock"  — current ChatDock contents (compact bar + log + composer)
//   "panel" — inline, no chrome (current ChatPanel look)
//   "full"  — full-page with fixed 260px thread sidebar + Back link

export type ChatMode = "data" | "build";
export type ChatVariant = "dock" | "panel" | "full";

export type ChatProps = {
  contextKey: string;
  mode?: ChatMode;
  variant?: ChatVariant;
  placeholder?: string;
  onAction?: () => void;
  // When true, the chat is bound to one app's contextKey and the provider list
  // is fetched scoped to it (only that app's assistant(s)). When false/omitted,
  // the full cross-context provider list is fetched (Agents / focused view).
  scoped?: boolean;
};

type ToolCall = { name: string; input: string };

function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

// A build agent's contextKey is "build:<slug>". The slug owns its task list per
// the backend contract; data/assistant and project: contexts have no such list.
function buildSlug(contextKey: string): string | null {
  return contextKey.startsWith("build:") ? contextKey.slice("build:".length) : null;
}

const TASK_STATUSES: AgentTaskStatus[] = ["todo", "doing", "done", "blocked"];
const TASK_STATUS_LABELS: Record<AgentTaskStatus, string> = {
  todo: "To do",
  doing: "Doing",
  done: "Done",
  blocked: "Blocked",
};

// Friendly labels for the model ids the backend routes by. Used as a fallback
// when a provider doesn't ship a label for a model id.
const MODEL_LABELS: Record<string, string> = {
  "claude-opus-4-8": "Opus 4.8",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};

function modelLabel(id: string): string {
  return MODEL_LABELS[id] ?? id;
}

// Best-effort detection of a network/egress block, so the builder shows a
// concise notice instead of a wall of stderr. We keep the raw detail available.
const EGRESS_MARKERS = [
  "egress",
  "blocked",
  "could not resolve host",
  "network is unreachable",
  "npm err! network",
  "fatal: unable to access",
  "temporary failure in name resolution",
  "connection refused",
];

function isEgressFailure(text: string | null | undefined): boolean {
  if (!text) return false;
  const t = text.toLowerCase();
  return EGRESS_MARKERS.some((m) => t.includes(m));
}

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction, scoped }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

  // Per-thread model selection. `selectedModel` is what the next send uses;
  // it's reset to the active provider's default / the thread's saved model on
  // thread switch and provider load.
  const [selectedModel, setSelectedModel] = useState<string>("");

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);

  const [pendingText, setPendingText] = useState("");
  const [pendingTools, setPendingTools] = useState<ToolCall[]>([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Bumped after a chat turn completes so the task panel refetches (the agent may
  // have created/updated tasks during the turn — same store).
  const [taskRefetchKey, setTaskRefetchKey] = useState(0);
  const slug = buildSlug(contextKey);

  const scrollRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  // Provider list. When `scoped`, fetch only the provider(s) relevant to this
  // app's contextKey; otherwise fetch the full cross-context list so the
  // operator can pick any. Load even where the picker is hidden so the pop-out
  // from a build view still resolves the label + default model.
  useEffect(() => {
    let cancelled = false;
    chatApi
      .listProviders(scoped ? contextKey : undefined)
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        if (effectiveMode === "data" && list.length && !list.some((p) => p.key === contextKey)) {
          setActiveProvider("general");
        }
      })
      .catch(() => { /* non-essential */ });
    return () => { cancelled = true; };
  }, [contextKey, effectiveMode, scoped]);

  // Scope key for thread list. In build mode this is the contextKey directly
  // (which is e.g. "build:my-agent"); in data mode the operator can swap.
  const scope = effectiveMode === "build" ? contextKey : activeProvider;

  // The provider whose model menu drives the picker. Build chats use the
  // contextKey provider; data chats use the operator-selected provider.
  const providerKey = effectiveMode === "build" ? contextKey : activeProvider;
  const activeProviderObj = useMemo(
    () => providers.find((p) => p.key === providerKey),
    [providers, providerKey],
  );
  const modelOptions = useMemo(
    () => activeProviderObj?.models ?? Object.keys(MODEL_LABELS),
    [activeProviderObj],
  );

  // When providers resolve (or the active provider changes) and we have no
  // model chosen yet for the current thread, fall back to the provider default.
  useEffect(() => {
    if (selectedModel && modelOptions.includes(selectedModel)) return;
    const def = activeProviderObj?.default_model;
    if (def && modelOptions.includes(def)) setSelectedModel(def);
    else if (modelOptions.length) setSelectedModel(modelOptions[0]);
  }, [activeProviderObj, modelOptions, selectedModel]);

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await chatApi.listThreads(scope));
    } catch (e) {
      setErr(String(e));
    }
  }, [scope]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setErr(null);
      setLoadingThreads(true);
      try {
        const list = await chatApi.listThreads(scope);
        if (cancelled) return;
        setThreads(list);
        if (list.length) {
          const t = await chatApi.getThread(list[0].id);
          if (cancelled) return;
          setActiveId(t.id);
          setMessages(t.messages);
          if (t.model) setSelectedModel(t.model);
        } else {
          setActiveId(null);
          setMessages([]);
        }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      } finally {
        if (!cancelled) setLoadingThreads(false);
      }
    })();
    return () => {
      cancelled = true;
      sseRef.current?.close();
      sseRef.current = null;
    };
  }, [scope]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingText, pendingTools.length, busy, streaming]);

  const openThread = useCallback(async (id: string) => {
    setErr(null);
    try {
      const t: Thread = await chatApi.getThread(id);
      setActiveId(t.id);
      setMessages(t.messages);
      // Reflect the thread's remembered model; if it has none, fall back to the
      // provider default via the seeding effect (clear the selection).
      setSelectedModel(t.model || "");
      setPendingText("");
      setPendingTools([]);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const newChat = useCallback(() => {
    setActiveId(null);
    setMessages([]);
    setPendingText("");
    setPendingTools([]);
    setErr(null);
    // Reset to the provider default for a fresh thread (seeding effect re-fills).
    setSelectedModel("");
    sseRef.current?.close();
    sseRef.current = null;
  }, []);

  const stop = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    setStreaming(false);
  }, []);

  const removeThread = useCallback(async (id: string) => {
    await chatApi.deleteThread(id);
    if (id === activeId) newChat();
    refreshThreads();
  }, [activeId, newChat, refreshThreads]);

  // After the first exchange the backend auto-generates a title asynchronously.
  // Refetch the thread list a moment later so the new title surfaces without a
  // manual reload. Only fire when the thread had no title (a fresh chat).
  const scheduleAutoNameRefetch = useCallback((wasFirstExchange: boolean) => {
    if (!wasFirstExchange) return;
    window.setTimeout(() => { void refreshThreads(); }, 2500);
  }, [refreshThreads]);

  const sendData = useCallback(async (text: string) => {
    setBusy(true);
    const isFirst = messages.length <= 1;
    try {
      let id = activeId;
      if (!id) {
        const t = await chatApi.createThread(scope);
        id = t.id;
        setActiveId(id);
      }
      const { message, notes } = await chatApi.sendMessage(id, text, selectedModel || undefined);
      setMessages((m) => [...m, message]);
      refreshThreads();
      scheduleAutoNameRefetch(isFirst);
      setTaskRefetchKey((k) => k + 1);
      if (notes && notes.length) onAction?.();
    } catch (e) {
      setErr(String(e));
      setMessages((m) => [
        ...m,
        { role: "assistant", content: "(failed to send - try again)", ts: new Date().toISOString(), meta: { error: true } },
      ]);
    } finally {
      setBusy(false);
    }
  }, [activeId, scope, refreshThreads, onAction, messages.length, selectedModel, scheduleAutoNameRefetch]);

  const sendBuild = useCallback(async (text: string) => {
    setPendingText("");
    setPendingTools([]);
    setStreaming(true);
    const isFirst = messages.length <= 1;
    try {
      let id = activeId;
      if (!id) {
        const t = await chatApi.createThread(scope);
        id = t.id;
        setActiveId(id);
      }
      const r = await fetch(`/api/chat/threads/${id}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selectedModel ? { content: text, model: selectedModel } : { content: text }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const { turn_id } = await r.json();

      const es = new EventSource(`/api/chat/turns/${turn_id}/stream`);
      sseRef.current = es;

      es.addEventListener("text", (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data);
          setPendingText((t) => t + (data.payload?.content || ""));
        } catch { /* ignore */ }
      });
      es.addEventListener("tool", (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data);
          setPendingTools((tools) => [...tools, { name: data.payload?.name || "?", input: data.payload?.input || "" }]);
        } catch { /* ignore */ }
      });
      es.addEventListener("error", (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data || "{}");
          if (data.payload?.detail) setErr(String(data.payload.detail));
        } catch { /* ignore */ }
      });
      const finish = async () => {
        es.close();
        sseRef.current = null;
        setStreaming(false);
        const id2 = activeId;
        const target = id2 || (await chatApi.listThreads(scope))[0]?.id;
        if (target) {
          try {
            const t = await chatApi.getThread(target);
            setMessages(t.messages);
          } catch { /* ignore */ }
        }
        setPendingText("");
        setPendingTools([]);
        refreshThreads();
        scheduleAutoNameRefetch(isFirst);
        setTaskRefetchKey((k) => k + 1);
      };
      es.addEventListener("done", finish);
      es.addEventListener("end", finish);
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          setStreaming(false);
          sseRef.current = null;
        }
      };
    } catch (e) {
      setErr(String(e));
      setStreaming(false);
    }
  }, [activeId, scope, refreshThreads, selectedModel, messages.length, scheduleAutoNameRefetch]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text) return;
    if (effectiveMode === "build" ? streaming : busy) return;
    setErr(null);
    setInput("");
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    if (effectiveMode === "build") sendBuild(text);
    else sendData(text);
  }, [input, effectiveMode, streaming, busy, sendBuild, sendData]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const popOut = () => {
    navigate(`/chat/${encodeURIComponent(contextKey)}`);
  };

  const back = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/agents");
  };

  const inFlight = effectiveMode === "build" ? streaming : busy;

  // Egress banner (build only). If the latest error or an assistant-error
  // message looks like a network/egress block, surface a concise notice instead
  // of raw stderr. The raw detail stays available in a collapsible <details>.
  const lastErrorMessage = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].meta?.error) return messages[i].content;
    }
    return null;
  }, [messages]);
  const egressDetail = isEgressFailure(err)
    ? err
    : isEgressFailure(lastErrorMessage)
      ? lastErrorMessage
      : null;
  const showEgressBanner = effectiveMode === "build" && egressDetail != null;

  const egressBanner = showEgressBanner ? (
    <div style={{ padding: "var(--space-2) var(--space-3)" }}>
      <StateBlock
        variant="error"
        icon={<WifiOff aria-hidden />}
        title="External network is blocked in this environment."
        hint={
          <>
            <span>
              Run network commands (git clone / npm install) in your own terminal, or
              approve the host in Settings.
            </span>
            <details style={{ marginTop: "var(--space-2)" }}>
              <summary className="ws-card__meta" style={{ cursor: "pointer" }}>Details</summary>
              <pre
                className="ws-mono"
                style={{
                  marginTop: "var(--space-2)", whiteSpace: "pre-wrap",
                  fontSize: "var(--fs-xs)", maxHeight: "12vh", overflow: "auto",
                }}
              >
                {egressDetail}
              </pre>
            </details>
          </>
        }
      />
    </div>
  ) : null;

  const composer = (
    <div className="chat-composer">
      <textarea
        className="chat-input"
        rows={1}
        value={input}
        placeholder={placeholder ?? (effectiveMode === "build" ? "Tell the agent what to build..." : "Message Clawd...")}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKey}
        disabled={inFlight}
      />
      <div className="chat-composer__controls">
        {modelOptions.length > 0 && (
          <Select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            aria-label="Model"
            title="Model for this conversation"
            className="chat-model"
          >
            {modelOptions.map((id) => (
              <option key={id} value={id}>{modelLabel(id)}</option>
            ))}
          </Select>
        )}
        <span className="chat-composer__spacer" />
        {variant !== "full" && (
          <Button icon onClick={popOut} aria-label="Expand chat" title="Expand chat">
            <Maximize2 aria-hidden />
          </Button>
        )}
        {effectiveMode === "build" && streaming ? (
          <Button variant="primary" icon onClick={stop} aria-label="Stop receiving (turn continues in background)" title="Stop receiving (turn continues in background)">
            <Square aria-hidden />
          </Button>
        ) : (
          <Button variant="primary" icon onClick={send} disabled={inFlight || !input.trim()} aria-label="Send (Enter)" title="Send (Enter)">
            <CornerDownLeft aria-hidden />
          </Button>
        )}
      </div>
    </div>
  );

  const threadBar = (
    <div className="chat-bar">
      {showProviderPicker && providers.length > 0 && effectiveMode === "data" && (
        <select
          className="chat-agent-select"
          value={activeProvider}
          onChange={(e) => setActiveProvider(e.target.value)}
          title="Which assistant to talk to"
        >
          {providers.map((p) => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </select>
      )}
      <select
        className="chat-thread-select"
        value={activeId ?? ""}
        onChange={(e) => (e.target.value ? openThread(e.target.value) : newChat())}
      >
        <option value="">New chat</option>
        {threads.map((t) => (
          <option key={t.id} value={t.id}>{t.title}</option>
        ))}
      </select>
      <button className="chat-icon-btn" title="New chat" onClick={newChat}>
        <Plus size={16} />
      </button>
      {activeId && (
        <button className="chat-icon-btn" title="Delete chat" onClick={() => setConfirmDeleteId(activeId)}>
          <Trash2 size={16} />
        </button>
      )}
    </div>
  );

  const confirmDeleteTitle = confirmDeleteId
    ? threads.find((t) => t.id === confirmDeleteId)?.title
    : undefined;

  const deleteDialog = (
    <ConfirmDialog
      open={confirmDeleteId != null}
      onCancel={() => setConfirmDeleteId(null)}
      onConfirm={async () => {
        const id = confirmDeleteId;
        setConfirmDeleteId(null);
        if (id) await removeThread(id);
      }}
      title="Delete chat?"
      body={
        <span>
          This permanently deletes {confirmDeleteTitle ? `"${confirmDeleteTitle}"` : "this chat"} and its
          messages. This can't be undone.
        </span>
      }
      confirmLabel="Delete chat"
    />
  );

  const overflowed = messages.length >= 500;

  const log = (
    <div className="chat-log" ref={scrollRef}>
      {loadingThreads && messages.length === 0 ? (
        <div className="chat-empty">Loading...</div>
      ) : err && messages.length === 0 ? (
        <div className="chat-empty" style={{ color: "var(--danger)" }}>Couldn't load threads. {err}</div>
      ) : messages.length === 0 && !inFlight ? (
        <div className="chat-empty">
          {effectiveMode === "build" ? "Tell the agent what to build..." : "Ask anything, or tell me to change the plan."}
        </div>
      ) : null}
      {overflowed && (
        <div className="chat-empty" style={{ fontSize: "var(--fs-xs)" }}>
          Showing the latest messages. Older history is paginated server-side.
        </div>
      )}
      {messages.map((m, i) => (
        <div key={i} className={`chat-msg ${m.role}`}>
          <div className={`chat-bubble ${m.meta?.error ? "error" : ""}`}>{m.content}</div>
          {m.meta?.notes?.map((n, j) => (
            <div key={j} className="chat-note">{n}</div>
          ))}
        </div>
      ))}
      {(busy || streaming || pendingText || pendingTools.length > 0) && (
        <div className="chat-msg assistant">
          <div className="chat-bubble">
            {pendingTools.length > 0 && (
              <div className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginBottom: "0.3rem" }}>
                {pendingTools.map((t, i) => (
                  <div key={i} className="ws-row" style={{ gap: "0.3rem" }}>
                    <Wrench size={11} aria-hidden />
                    <span className="ws-mono">{t.name}</span>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.input}</span>
                  </div>
                ))}
              </div>
            )}
            {pendingText || (inFlight ? "..." : "")}
          </div>
        </div>
      )}
    </div>
  );

  const taskPanel = slug ? <AgentTaskPanel slug={slug} refetchKey={taskRefetchKey} /> : null;

  const panelInner = (
    <div className="chat-panel">
      {threadBar}
      {taskPanel}
      {log}
      {egressBanner}
      {err && messages.length > 0 && !showEgressBanner && <div className="chat-err">{err}</div>}
      {composer}
      {deleteDialog}
    </div>
  );

  if (variant === "full") {
    const providerLabel = providers.find((p) => p.key === contextKey)?.label;
    return (
      <div className="ws-focused-chat">
        <aside className="ws-focused-chat__sidebar" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", overflow: "auto" }}>
          <Link to="#" onClick={(e) => { e.preventDefault(); back(); }} className="ws-bc__link" style={{ display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
            <ChevronLeft size={14} aria-hidden /> Back
          </Link>
          <div className="ws-card__meta ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
            {providerLabel ?? contextKey}
          </div>
          <button className="chat-icon-btn" onClick={newChat} title="New chat" style={{ alignSelf: "flex-start" }}>
            <Plus size={14} aria-hidden /> New chat
          </button>
          <div style={{ flex: 1, overflow: "auto", borderTop: "1px solid var(--border)", paddingTop: "var(--space-2)" }}>
            {loadingThreads ? (
              <p className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>Loading threads...</p>
            ) : threads.length === 0 ? (
              <p className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>No chats yet.</p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {threads.map((t) => (
                  <li key={t.id}>
                    <button
                      onClick={() => openThread(t.id)}
                      className="chat-icon-btn"
                      style={{
                        width: "100%", justifyContent: "flex-start", textAlign: "left",
                        background: t.id === activeId ? "var(--accent-soft)" : "transparent",
                        border: 0, padding: "0.4rem 0.5rem", marginBottom: "0.2rem",
                        fontSize: "var(--fs-sm)",
                      }}
                    >
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {panelInner}
        </div>
      </div>
    );
  }

  return panelInner;
}

// ---- Per-agent task panel (build chats only) -------------------------------
// A compact, collapsible section listing the build agent's own tasks. Operator
// can add tasks and cycle their status; the agent writes the same store, so we
// refetch on mount, on `refetchKey` change (after a turn completes), and after
// every mutation. Done tasks are hidden until the "show done" toggle is on.
function AgentTaskPanel({ slug, refetchKey }: { slug: string; refetchKey: number }) {
  const [open, setOpen] = useState(false);
  const [showDone, setShowDone] = useState(false);
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      setTasks(await chatApi.listAgentTasks(slug, showDone));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [slug, showDone]);

  // Refetch on mount, when scope/showDone changes, and after each turn. Keep the
  // skeleton only for the very first load so background refetches don't flicker.
  useEffect(() => {
    void load();
  }, [load, refetchKey]);

  const setStatus = useCallback(
    async (id: string, status: AgentTaskStatus) => {
      // Optimistic: reflect immediately, then reconcile from the server.
      setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, status } : t)));
      try {
        await chatApi.updateAgentTask(slug, id, { status });
      } catch (e) {
        setErr(String(e));
      } finally {
        void load();
      }
    },
    [slug, load],
  );

  const remove = useCallback(
    async (id: string) => {
      setTasks((prev) => prev.filter((t) => t.id !== id));
      try {
        await chatApi.deleteAgentTask(slug, id);
      } catch (e) {
        setErr(String(e));
      } finally {
        void load();
      }
    },
    [slug, load],
  );

  const add = useCallback(async () => {
    const text = draft.trim();
    if (!text || adding) return;
    setAdding(true);
    setErr(null);
    try {
      await chatApi.addAgentTask(slug, text);
      setDraft("");
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setAdding(false);
    }
  }, [draft, adding, slug, load]);

  const OVERFLOW = 100;
  const visible = tasks.slice(0, OVERFLOW);
  const openCount = tasks.filter((t) => t.status !== "done").length;

  let body: React.ReactNode;
  if (loading) {
    body = (
      <div className="chat-tasks__list" aria-busy="true">
        <Skeleton width="80%" />
        <Skeleton width="60%" />
        <Skeleton width="70%" />
      </div>
    );
  } else if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load tasks."
        hint={
          <>
            <span className="ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
              GET /api/agents/build/{slug}/tasks
            </span>
            <div style={{ marginTop: "var(--space-2)" }}>
              <Button size="sm" variant="ghost" onClick={() => { setLoading(true); void load(); }}>
                Retry
              </Button>
            </div>
          </>
        }
      />
    );
  } else if (tasks.length === 0) {
    body = (
      <StateBlock
        title="No tasks yet."
        hint="Add one below, or the agent will add tasks as it works on them here."
      />
    );
  } else {
    body = (
      <>
        <ul className="chat-tasks__list">
          {visible.map((t) => (
            <li key={t.id} className={`chat-task${t.status === "done" ? " chat-task--done" : ""}`}>
              <Select
                className="chat-task__status"
                value={t.status}
                onChange={(e) => setStatus(t.id, e.target.value as AgentTaskStatus)}
                aria-label={`Status for ${t.text}`}
                title="Task status"
              >
                {TASK_STATUSES.map((s) => (
                  <option key={s} value={s}>{TASK_STATUS_LABELS[s]}</option>
                ))}
              </Select>
              <span className="chat-task__text">{t.text}</span>
              <Button
                size="sm"
                variant="ghost"
                icon
                onClick={() => remove(t.id)}
                aria-label={`Delete task ${t.text}`}
                title="Delete task"
              >
                <X aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
        {tasks.length > OVERFLOW && (
          <div className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            Showing the first {OVERFLOW} of {tasks.length} tasks.
          </div>
        )}
      </>
    );
  }

  return (
    <section className="chat-tasks">
      <button
        type="button"
        className="chat-tasks__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="chat-tasks__chev">
          {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
        </span>
        <ListTodo size={14} aria-hidden />
        <span className="chat-tasks__title">Tasks</span>
        <span className="chat-tasks__count">{openCount} open</span>
      </button>
      {open && (
        <div className="chat-tasks__body">
          <div className="chat-tasks__toolbar">
            <Toggle checked={showDone} onChange={setShowDone} label="Show done" />
          </div>
          {body}
          <div className="chat-tasks__add">
            <Input
              value={draft}
              placeholder="Add a task..."
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void add();
                }
              }}
              aria-label="New task"
            />
            <Button
              variant="primary"
              icon
              onClick={() => void add()}
              disabled={adding || !draft.trim()}
              aria-label="Add task"
              title="Add task"
            >
              <Plus aria-hidden />
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
