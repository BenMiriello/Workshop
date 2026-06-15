import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus, Trash2, CornerDownLeft, Square, Wrench, Maximize2, ChevronLeft,
  WifiOff, ChevronRight, X, ListTodo,
} from "lucide-react";
import { ConfirmDialog, StateBlock, Skeleton, Input, Toggle, Button, Select, Markdown } from "../ui";
import {
  chatApi,
  type ChatMsg,
  type ChatSegment,
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
//   "dock"  — floating bottom-sheet contents
//   "panel" — inline, fills its host region (build IDE chat column)
//   "full"  — full-page, centred, fills the viewport (FocusedChat route)
//
// All variants share ONE compact toolbar (conversation picker + new/delete +
// tasks + model + expand) so nothing stacks into wasted vertical rows, and the
// composer is an auto-growing textarea (~2.5 lines) that stays editable while a
// turn is streaming. Assistant turns render as ordered text/tool segments that
// survive a reload and reconnect to the live stream.

export type ChatMode = "data" | "build";
export type ChatVariant = "dock" | "panel" | "full";

export type ChatProps = {
  contextKey: string;
  mode?: ChatMode;
  variant?: ChatVariant;
  placeholder?: string;
  onAction?: () => void;
  scoped?: boolean;
};

function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

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

const MODEL_LABELS: Record<string, string> = {
  "claude-opus-4-8": "Opus 4.8",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};
function modelLabel(id: string): string {
  return MODEL_LABELS[id] ?? id;
}

const EGRESS_MARKERS = [
  "egress", "blocked", "could not resolve host", "network is unreachable",
  "npm err! network", "fatal: unable to access",
  "temporary failure in name resolution", "connection refused",
];
function isEgressFailure(text: string | null | undefined): boolean {
  if (!text) return false;
  const t = text.toLowerCase();
  return EGRESS_MARKERS.some((m) => t.includes(m));
}

// ---- Tool call: compact by default, tap to expand -------------------------
// One dense row (icon · name · truncated input). Tapping reveals the full input
// (a command / path / pattern). Mobile-first: the preview truncates with an
// ellipsis so a tool call never blows out the bubble width.
function ToolChip({ name, input }: { name: string; input: string }) {
  const [open, setOpen] = useState(false);
  const hasDetail = !!input;
  return (
    <div className={`chat-tool${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="chat-tool__head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        title={hasDetail ? input : name}
      >
        {hasDetail && <ChevronRight size={13} aria-hidden className="chat-tool__chev" />}
        <Wrench size={12} aria-hidden className="chat-tool__icon" />
        <span className="chat-tool__name">{name}</span>
        {hasDetail && <span className="chat-tool__preview ws-mono">{input}</span>}
      </button>
      {open && hasDetail && <pre className="chat-tool__detail ws-mono">{input}</pre>}
    </div>
  );
}

// Render an assistant turn from its ordered segments: text runs as markdown,
// tool calls as compact chips, preserving interleaving.
function MessageSegments({ segments }: { segments: ChatSegment[] }) {
  return (
    <>
      {segments.map((s, i) =>
        s.t === "text" ? (
          s.text.trim() ? <Markdown key={i}>{s.text}</Markdown> : null
        ) : (
          <ToolChip key={i} name={s.name} input={s.input} />
        ),
      )}
    </>
  );
}

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction, scoped }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

  const [selectedModel, setSelectedModel] = useState<string>("");

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);

  // Live assistant turn: ordered segments built from the SSE stream.
  const [pendingSegments, setPendingSegments] = useState<ChatSegment[]>([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const [taskRefetchKey, setTaskRefetchKey] = useState(0);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [openTaskCount, setOpenTaskCount] = useState(0);
  const slug = buildSlug(contextKey);

  const scrollRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const finishingRef = useRef(false);

  // Provider list (scoped to this app, or the full cross-context list).
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

  const scope = effectiveMode === "build" ? contextKey : activeProvider;
  const providerKey = effectiveMode === "build" ? contextKey : activeProvider;
  const activeProviderObj = useMemo(
    () => providers.find((p) => p.key === providerKey),
    [providers, providerKey],
  );
  const modelOptions = useMemo(
    () => activeProviderObj?.models ?? Object.keys(MODEL_LABELS),
    [activeProviderObj],
  );

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

  const scheduleAutoNameRefetch = useCallback((wasFirstExchange: boolean) => {
    if (!wasFirstExchange) return;
    window.setTimeout(() => { void refreshThreads(); }, 2500);
  }, [refreshThreads]);

  // ---- Live SSE stream (build turns) --------------------------------------
  // Used both for a freshly-started turn and for reconnecting to an in-flight
  // turn after a reload (the server replays all events via Last-Event-ID=0).
  const attachStream = useCallback(
    (turnId: string, threadId: string, isFirst: boolean) => {
      sseRef.current?.close();
      finishingRef.current = false;
      setStreaming(true);
      setPendingSegments([]);

      const pushText = (content: string) =>
        setPendingSegments((segs) => {
          if (!content) return segs;
          const last = segs[segs.length - 1];
          if (last && last.t === "text") {
            const copy = segs.slice();
            copy[copy.length - 1] = { t: "text", text: last.text + content };
            return copy;
          }
          return [...segs, { t: "text", text: content }];
        });

      const es = new EventSource(`/api/chat/turns/${turnId}/stream`);
      sseRef.current = es;

      es.addEventListener("text", (e) => {
        try { pushText(JSON.parse((e as MessageEvent).data).payload?.content || ""); } catch { /* ignore */ }
      });
      es.addEventListener("tool", (e) => {
        try {
          const p = JSON.parse((e as MessageEvent).data).payload || {};
          setPendingSegments((s) => [...s, { t: "tool", name: p.name || "?", input: p.input || "" }]);
        } catch { /* ignore */ }
      });
      es.addEventListener("error", (e) => {
        try {
          const d = JSON.parse((e as MessageEvent).data || "{}");
          if (d.payload?.detail) setErr(String(d.payload.detail));
        } catch { /* ignore */ }
      });

      const finish = async () => {
        if (finishingRef.current) return;
        finishingRef.current = true;
        es.close();
        sseRef.current = null;
        setStreaming(false);
        try {
          const t = await chatApi.getThread(threadId);
          setMessages(t.messages);
        } catch { /* ignore */ }
        setPendingSegments([]);
        refreshThreads();
        scheduleAutoNameRefetch(isFirst);
        setTaskRefetchKey((k) => k + 1);
      };
      es.addEventListener("done", finish);
      es.addEventListener("end", finish);
      es.onerror = () => {
        // CLOSED = server ended/failed (incl. a 404 for a turn that's already
        // gone). Sync from the persisted thread and unlock the composer.
        if (es.readyState === EventSource.CLOSED) void finish();
      };
    },
    [refreshThreads, scheduleAutoNameRefetch],
  );

  // Render a thread's messages, reconnecting to a still-streaming last turn.
  const hydrate = useCallback(
    (t: Thread) => {
      const msgs = t.messages || [];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.meta?.streaming && last.meta.turn_id) {
        setMessages(msgs.slice(0, -1));
        attachStream(last.meta.turn_id, t.id, false);
      } else {
        setMessages(msgs);
        setPendingSegments([]);
      }
    },
    [attachStream],
  );

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
          if (t.model) setSelectedModel(t.model);
          hydrate(t);
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
    // hydrate is stable enough; scope drives the reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, pendingSegments, busy, streaming]);

  // Auto-grow the composer: 2.5 lines tall by default, grows with content.
  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // Grow to fit content, capped at the CSS max-height (3 rows) — read it so
    // the JS cap can't drift from the stylesheet; beyond that it scrolls.
    const max = parseFloat(getComputedStyle(el).maxHeight) || 96;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, []);
  useEffect(() => { autosize(); }, [input, autosize]);

  const openThread = useCallback(async (id: string) => {
    setErr(null);
    sseRef.current?.close();
    sseRef.current = null;
    try {
      const t: Thread = await chatApi.getThread(id);
      setActiveId(t.id);
      setSelectedModel(t.model || "");
      hydrate(t);
    } catch (e) {
      setErr(String(e));
    }
  }, [hydrate]);

  const newChat = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    setActiveId(null);
    setMessages([]);
    setPendingSegments([]);
    setErr(null);
    setSelectedModel("");
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
    setStreaming(true);
    setPendingSegments([]);
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
      attachStream(turn_id, id, isFirst);
    } catch (e) {
      setErr(String(e));
      setStreaming(false);
    }
  }, [activeId, scope, selectedModel, messages.length, attachStream]);

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

  const popOut = () => navigate(`/chat/${encodeURIComponent(contextKey)}`);
  const back = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate("/agents");
  };

  const inFlight = effectiveMode === "build" ? streaming : busy;

  // Egress banner (build only).
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
    <div className="chat-egress">
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
            <details className="chat-egress__details">
              <summary className="ws-card__meta">Details</summary>
              <pre className="ws-mono chat-egress__pre">{egressDetail}</pre>
            </details>
          </>
        }
      />
    </div>
  ) : null;

  // ---- Compact toolbar: one row for picker + new/delete + tasks + model ----
  const toolbar = (
    <div className="chat-toolbar">
      {variant === "full" && (
        <button className="chat-tb__btn" onClick={back} title="Back" aria-label="Back">
          <ChevronLeft size={16} aria-hidden />
        </button>
      )}
      {showProviderPicker && providers.length > 0 && effectiveMode === "data" && (
        <select
          className="chat-tb__select chat-tb__provider"
          value={activeProvider}
          onChange={(e) => setActiveProvider(e.target.value)}
          title="Which assistant to talk to"
          aria-label="Assistant"
        >
          {providers.map((p) => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </select>
      )}
      <select
        className="chat-tb__select chat-tb__thread"
        value={activeId ?? ""}
        onChange={(e) => (e.target.value ? openThread(e.target.value) : newChat())}
        title="Conversation"
        aria-label="Conversation"
      >
        <option value="">New chat</option>
        {threads.map((t) => (
          <option key={t.id} value={t.id}>{t.title}</option>
        ))}
      </select>
      <button className="chat-tb__btn" onClick={newChat} title="New chat" aria-label="New chat">
        <Plus size={16} aria-hidden />
      </button>
      {activeId && (
        <button className="chat-tb__btn" onClick={() => setConfirmDeleteId(activeId)} title="Delete chat" aria-label="Delete chat">
          <Trash2 size={16} aria-hidden />
        </button>
      )}
      {slug && (
        <button
          className={`chat-tb__btn chat-tb__tasks${tasksOpen ? " is-active" : ""}`}
          onClick={() => setTasksOpen((o) => !o)}
          title="Tasks"
          aria-label="Tasks"
          aria-expanded={tasksOpen}
        >
          <ListTodo size={16} aria-hidden />
          {openTaskCount > 0 && <span className="chat-tb__badge">{openTaskCount}</span>}
        </button>
      )}
      <span className="chat-tb__spacer" />
      {modelOptions.length > 0 && (
        <select
          className="chat-tb__select chat-tb__model"
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          title="Model for this conversation"
          aria-label="Model"
        >
          {modelOptions.map((id) => (
            <option key={id} value={id}>{modelLabel(id)}</option>
          ))}
        </select>
      )}
      {variant !== "full" && (
        <button className="chat-tb__btn" onClick={popOut} title="Open full screen" aria-label="Open full screen">
          <Maximize2 size={16} aria-hidden />
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

  const renderAssistant = (m: ChatMsg) => {
    if (m.meta?.error) return m.content;
    if (m.meta?.segments && m.meta.segments.length) return <MessageSegments segments={m.meta.segments} />;
    return <Markdown>{m.content}</Markdown>;
  };

  const log = (
    <div className="chat-log" ref={scrollRef}>
      {loadingThreads && messages.length === 0 ? (
        <div className="chat-empty">Loading...</div>
      ) : err && messages.length === 0 ? (
        <div className="chat-empty chat-empty--err">Couldn't load threads. {err}</div>
      ) : messages.length === 0 && !inFlight ? (
        <div className="chat-empty">
          {effectiveMode === "build" ? "Tell the agent what to build..." : "Ask anything, or tell me to change the plan."}
        </div>
      ) : null}
      {overflowed && (
        <div className="chat-empty chat-empty--note">
          Showing the latest messages. Older history is paginated server-side.
        </div>
      )}
      {messages.map((m, i) => (
        <div key={i} className={`chat-msg ${m.role}`}>
          <div className={`chat-bubble ${m.meta?.error ? "error" : ""}`}>
            {m.role === "assistant" ? renderAssistant(m) : m.content}
          </div>
          {m.meta?.notes?.map((n, j) => (
            <div key={j} className="chat-note">{n}</div>
          ))}
        </div>
      ))}
      {(busy || streaming || pendingSegments.length > 0) && (
        <div className="chat-msg assistant">
          <div className="chat-bubble">
            {pendingSegments.length > 0 && <MessageSegments segments={pendingSegments} />}
            {(streaming || busy) && <span className="chat-typing" aria-label="working">•••</span>}
          </div>
        </div>
      )}
    </div>
  );

  const taskPanel = slug ? (
    <AgentTaskPanel
      slug={slug}
      refetchKey={taskRefetchKey}
      open={tasksOpen}
      onCount={setOpenTaskCount}
    />
  ) : null;

  const composer = (
    <div className="chat-composer">
      <textarea
        ref={inputRef}
        className="chat-input"
        rows={1}
        value={input}
        placeholder={placeholder ?? (effectiveMode === "build" ? "Tell the agent what to build..." : "Message Clawd...")}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKey}
      />
      {effectiveMode === "build" && streaming ? (
        <button className="chat-send" onClick={stop} aria-label="Stop receiving (turn continues in background)" title="Stop receiving (turn continues in background)">
          <Square size={16} aria-hidden />
        </button>
      ) : (
        <button className="chat-send" onClick={send} disabled={inFlight || !input.trim()} aria-label="Send (Enter)" title="Send (Enter)">
          <CornerDownLeft size={16} aria-hidden />
        </button>
      )}
    </div>
  );

  const panelInner = (
    <div className={`chat-panel chat-panel--${variant}`}>
      {toolbar}
      {taskPanel}
      {log}
      {egressBanner}
      {err && messages.length > 0 && !showEgressBanner && <div className="chat-err">{err}</div>}
      {composer}
      {deleteDialog}
    </div>
  );

  if (variant === "full") {
    return <div className="ws-chat-full">{panelInner}</div>;
  }
  return panelInner;
}

// ---- Per-agent task panel (build chats only) -------------------------------
// The toolbar owns the open/close toggle + count badge; this renders only the
// drawer body when open, and reports the open-task count up for the badge.
function AgentTaskPanel({
  slug, refetchKey, open, onCount,
}: {
  slug: string;
  refetchKey: number;
  open: boolean;
  onCount: (n: number) => void;
}) {
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

  useEffect(() => { void load(); }, [load, refetchKey]);
  useEffect(() => { onCount(tasks.filter((t) => t.status !== "done").length); }, [tasks, onCount]);

  const setStatus = useCallback(
    async (id: string, status: AgentTaskStatus) => {
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

  if (!open) return null;

  const OVERFLOW = 100;
  const visible = tasks.slice(0, OVERFLOW);

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
    <div className="chat-tasks">
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
    </div>
  );
}
