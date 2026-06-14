import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Trash2, CornerDownLeft, Square, Wrench, Maximize2, ChevronLeft } from "lucide-react";
import { Button, ConfirmDialog } from "../ui";
import { chatApi, type ChatMsg, type Thread, type ThreadSummary, type Provider } from "../lib/chat";

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
};

type ToolCall = { name: string; input: string };

function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

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

  const scrollRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  // Provider list (only relevant where the picker shows; load anyway so the
  // pop-out from a build view still resolves the label).
  useEffect(() => {
    let cancelled = false;
    chatApi
      .listProviders()
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        if (effectiveMode === "data" && list.length && !list.some((p) => p.key === contextKey)) {
          setActiveProvider("general");
        }
      })
      .catch(() => { /* non-essential */ });
    return () => { cancelled = true; };
  }, [contextKey, effectiveMode]);

  // Scope key for thread list. In build mode this is the contextKey directly
  // (which is e.g. "build:my-agent"); in data mode the operator can swap.
  const scope = effectiveMode === "build" ? contextKey : activeProvider;

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

  const sendData = useCallback(async (text: string) => {
    setBusy(true);
    try {
      let id = activeId;
      if (!id) {
        const t = await chatApi.createThread(scope);
        id = t.id;
        setActiveId(id);
      }
      const { message, notes } = await chatApi.sendMessage(id, text);
      setMessages((m) => [...m, message]);
      refreshThreads();
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
  }, [activeId, scope, refreshThreads, onAction]);

  const sendBuild = useCallback(async (text: string) => {
    setPendingText("");
    setPendingTools([]);
    setStreaming(true);
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
        body: JSON.stringify({ content: text }),
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
  }, [activeId, scope, refreshThreads]);

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

  const composer = (
    <div className="chat-input-row">
      <textarea
        className="chat-input"
        rows={1}
        value={input}
        placeholder={placeholder ?? (effectiveMode === "build" ? "Tell the agent what to build..." : "Message Clawd...")}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKey}
        disabled={inFlight}
      />
      {effectiveMode === "build" && streaming ? (
        <Button variant="primary" icon onClick={stop} aria-label="Stop receiving (turn continues in background)" title="Stop receiving (turn continues in background)">
          <Square aria-hidden />
        </Button>
      ) : (
        <Button variant="primary" icon onClick={send} disabled={inFlight || !input.trim()} aria-label="Send (Enter)" title="Send (Enter)">
          <CornerDownLeft aria-hidden />
        </Button>
      )}
      {variant !== "full" && (
        <Button icon onClick={popOut} aria-label="Expand chat" title="Expand chat">
          <Maximize2 aria-hidden />
        </Button>
      )}
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

  const panelInner = (
    <div className="chat-panel">
      {threadBar}
      {log}
      {err && messages.length > 0 && <div className="chat-err">{err}</div>}
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
