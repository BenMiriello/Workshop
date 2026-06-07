import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, Trash2, CornerDownLeft } from "lucide-react";
import { chatApi, type ChatMsg, type Thread, type ThreadSummary, type Provider } from "../lib/chat";

// Reusable in-app chat. Drop it anywhere with a `defaultContextKey` — the host
// app's context "lives here" by default but the user can swap to any other
// registered provider via the agent picker. Each provider gets its own backend
// persona + live context + write actions.
//
// Threads are per-provider: switching provider switches the thread list and
// creates new threads under the chosen scope. This keeps "talk to budget" and
// "talk to commute" as distinct histories instead of one mushy stream.
//
// App-agnostic by design: drop it in any page with a sensible default scope.

type Props = {
  // Which provider this panel opens in. The host page passes its app's key
  // ("budget" inside Budget.tsx, "commute" inside Commute.tsx, etc.); the
  // panel auto-selects that provider on mount.
  contextKey: string;
  placeholder?: string;
  // Fired when a turn applied a write action (e.g. a plan edit), so the host
  // app can refresh its view. App-agnostic — host decides what to do.
  onAction?: () => void;
};

export default function ChatPanel({ contextKey, placeholder, onAction }: Props) {
  const [providers, setProviders] = useState<Provider[]>([]);
  // Active provider — defaults to the host's contextKey, but the user can
  // change it via the agent picker. Threads are listed per active provider.
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load providers once on mount; the host page determines initial provider.
  useEffect(() => {
    (async () => {
      try {
        const list = await chatApi.listProviders();
        setProviders(list);
        // If the host's contextKey isn't a known provider, fall back to general
        // rather than render an empty selector on an unknown scope.
        if (list.length && !list.some((p) => p.key === contextKey)) {
          setActiveProvider("general");
        }
      } catch (e) {
        // Provider list is non-essential; the chat works without it.
        // eslint-disable-next-line no-console
        console.warn("chat providers failed:", e);
      }
    })();
  }, [contextKey]);

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await chatApi.listThreads(activeProvider));
    } catch (e) {
      setErr(String(e));
    }
  }, [activeProvider]);

  // Whenever activeProvider changes: reload thread list and open the most
  // recent thread for that scope (if any).
  useEffect(() => {
    (async () => {
      setErr(null);
      try {
        const list = await chatApi.listThreads(activeProvider);
        setThreads(list);
        if (list.length) {
          const t = await chatApi.getThread(list[0].id);
          setActiveId(t.id);
          setMessages(t.messages);
        } else {
          setActiveId(null);
          setMessages([]);
        }
      } catch (e) {
        setErr(String(e));
      }
    })();
  }, [activeProvider]);

  // Keep the transcript pinned to the latest message.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  const openThread = useCallback(async (id: string) => {
    setErr(null);
    try {
      const t: Thread = await chatApi.getThread(id);
      setActiveId(t.id);
      setMessages(t.messages);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const newChat = useCallback(() => {
    setActiveId(null);
    setMessages([]);
    setErr(null);
  }, []);

  const removeThread = useCallback(
    async (id: string) => {
      await chatApi.deleteThread(id);
      if (id === activeId) newChat();
      refreshThreads();
    },
    [activeId, newChat, refreshThreads],
  );

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setErr(null);
    setSending(true);
    setInput("");

    // Optimistically render the user's line.
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);

    try {
      let id = activeId;
      if (!id) {
        const t = await chatApi.createThread(activeProvider);
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
        { role: "assistant", content: "(failed to send — try again)", ts: new Date().toISOString(), meta: { error: true } },
      ]);
    } finally {
      setSending(false);
    }
  }, [input, sending, activeId, activeProvider, refreshThreads, onAction]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="chat-panel">
      <div className="chat-bar">
        {/* Agent picker — defaults to host app's provider, user can swap. */}
        {providers.length > 0 && (
          <select
            className="chat-agent-select"
            value={activeProvider}
            onChange={(e) => setActiveProvider(e.target.value)}
            title="Which assistant to talk to"
          >
            {providers.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
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
            <option key={t.id} value={t.id}>
              {t.title}
            </option>
          ))}
        </select>
        <button className="chat-icon-btn" title="New chat" onClick={newChat}>
          <Plus size={16} />
        </button>
        {activeId && (
          <button className="chat-icon-btn" title="Delete chat" onClick={() => removeThread(activeId)}>
            <Trash2 size={16} />
          </button>
        )}
      </div>

      <div className="chat-log" ref={scrollRef}>
        {messages.length === 0 && !sending && (
          <div className="chat-empty">Ask anything, or tell me to change the plan.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            <div className={`chat-bubble ${m.meta?.error ? "error" : ""}`}>{m.content}</div>
            {m.meta?.notes?.map((n, j) => (
              <div key={j} className="chat-note">{n}</div>
            ))}
          </div>
        ))}
        {sending && (
          <div className="chat-msg assistant">
            <div className="chat-bubble chat-typing">…</div>
          </div>
        )}
      </div>

      {err && <div className="chat-err">{err}</div>}

      <div className="chat-input-row">
        <textarea
          className="chat-input"
          rows={1}
          value={input}
          placeholder={placeholder ?? "Message Clawd…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          disabled={sending}
        />
        <button className="chat-send" onClick={send} disabled={sending || !input.trim()} title="Send (Enter)">
          <CornerDownLeft size={16} />
        </button>
      </div>
    </div>
  );
}
