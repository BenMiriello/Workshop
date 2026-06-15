import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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

// ---- Per-model display handling -------------------------------------------
// How aggressively a model's turns collapse in the transcript. Keyed by model
// id with a `default` fallback, so a newly-added model can be tuned (or render
// differently) without touching the components.
type ModelDisplay = {
  headLines: number;       // lines kept at the top when a block is elided
  tailLines: number;       // lines kept at the bottom
  elideOverLines: number;  // elide only when a block exceeds this many lines
  recentSteps: number;     // work steps shown inline on the most recent turn
};
const MODEL_DISPLAY: Record<string, ModelDisplay> = {
  default: { headLines: 6, tailLines: 3, elideOverLines: 12, recentSteps: 2 },
};
function modelDisplay(model?: string | null): ModelDisplay {
  return (model && MODEL_DISPLAY[model]) || MODEL_DISPLAY.default;
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

// First-several + last-few line elision with Show more/less. `render` turns a
// source slice into nodes (markdown for assistant text, plain for user text).
// `expanded` seeds the open state (the most recent turn opens fully).
function ElidedContent({
  source, render, cfg, expanded,
}: {
  source: string;
  render: (s: string) => ReactNode;
  cfg: ModelDisplay;
  expanded?: boolean;
}) {
  const lines = source.split("\n");
  // Estimate *visual* lines: a long paragraph with no newlines still wraps to
  // many lines, so count wrapped rows (~CPL chars each), not just "\n"s.
  const CPL = 46; // ~chars per line on a phone; over-counts a little on desktop
  const visualLines = lines.reduce((n, l) => n + Math.max(1, Math.ceil(l.length / CPL)), 0);
  const elide = visualLines > Math.max(cfg.elideOverLines, cfg.headLines + cfg.tailLines + 1);
  const [open, setOpen] = useState(!!expanded);
  useEffect(() => { setOpen(!!expanded); }, [expanded]);
  if (!elide) return <>{render(source)}</>;
  if (open) {
    return (
      <>
        {render(source)}
        <button type="button" className="chat-more" onClick={() => setOpen(false)}>Show less</button>
      </>
    );
  }
  // Prefer slicing on real line breaks (lists, paragraphs, code); fall back to
  // characters when it's a few long wrapped lines so the preview still trims.
  let headSrc: string, tailSrc: string, label: string;
  if (lines.length >= cfg.headLines + cfg.tailLines + 2) {
    headSrc = lines.slice(0, cfg.headLines).join("\n");
    tailSrc = lines.slice(-cfg.tailLines).join("\n");
    label = `… Show ${lines.length - cfg.headLines - cfg.tailLines} more lines`;
  } else {
    headSrc = source.slice(0, cfg.headLines * CPL);
    tailSrc = source.slice(-cfg.tailLines * CPL);
    label = "… Show more";
  }
  return (
    <>
      {render(headSrc)}
      <button type="button" className="chat-more" onClick={() => setOpen(true)}>{label}</button>
      {render(tailSrc)}
    </>
  );
}

// The "work" of an assistant turn — its tool calls + interim text. On the most
// recent turn the latest few steps stay inline and older ones fold into a
// re-openable accordion; on older turns all of it folds away (just the answer
// shows). Tool calls themselves stay individually expandable (ToolChip).
function WorkSteps({ work, isLatest, cfg }: { work: ChatSegment[]; isLatest: boolean; cfg: ModelDisplay }) {
  const [open, setOpen] = useState(false);
  const inlineCount = isLatest ? cfg.recentSteps : 0;
  const older = inlineCount > 0 ? work.slice(0, Math.max(0, work.length - inlineCount)) : work;
  const recent = inlineCount > 0 ? work.slice(Math.max(0, work.length - inlineCount)) : [];
  const olderTools = older.filter((s) => s.t === "tool").length;
  const renderSeg = (s: ChatSegment, i: number) =>
    s.t === "tool"
      ? <ToolChip key={i} name={s.name} input={s.input} />
      : (s.text.trim() ? <div key={i} className="chat-work__text"><Markdown>{s.text}</Markdown></div> : null);
  return (
    <div className="chat-work">
      {older.length > 0 && (
        <>
          <button
            type="button"
            className={`chat-work__toggle${open ? " is-open" : ""}`}
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            <ChevronRight size={13} aria-hidden className="chat-work__chev" />
            {open ? "Hide" : "Show"} {older.length} earlier step{older.length > 1 ? "s" : ""}
            {olderTools > 0 ? ` · ${olderTools} tool${olderTools > 1 ? "s" : ""}` : ""}
          </button>
          {open && <div className="chat-work__list">{older.map(renderSeg)}</div>}
        </>
      )}
      {recent.length > 0 && <div className="chat-work__list">{recent.map(renderSeg)}</div>}
    </div>
  );
}

// An assistant turn: split the trailing run of text (the answer) from the work
// (tools + interim thinking). The answer renders fully on the most recent turn
// and is elided on older ones; the work collapses per WorkSteps.
function AssistantTurn({ segments, isLatest, model }: { segments: ChatSegment[]; isLatest: boolean; model?: string }) {
  const cfg = modelDisplay(model);
  let cut = segments.length;
  while (cut > 0 && segments[cut - 1].t === "text") cut--;
  const work = segments.slice(0, cut);
  const answer = segments.slice(cut);
  return (
    <>
      {work.length > 0 && <WorkSteps work={work} isLatest={isLatest} cfg={cfg} />}
      {answer.map((s, i) =>
        s.t === "text" && s.text.trim() ? (
          <ElidedContent
            key={i}
            source={s.text}
            cfg={cfg}
            expanded={isLatest}
            render={(src) => <Markdown>{src}</Markdown>}
          />
        ) : null,
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

  // Messages typed while a build turn is streaming are queued (a session can't
  // run two turns at once) and sent automatically when the turn finishes. They
  // render as pending user bubbles below the live response.
  const [queued, setQueued] = useState<string[]>([]);
  const queuedRef = useRef<string[]>([]);
  const sendBuildRef = useRef<(t: string) => void>(() => {});

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

  const setQueue = useCallback((next: string[]) => {
    queuedRef.current = next;
    setQueued(next);
  }, []);

  // Send the next queued message (if any) as a fresh turn. Called when a turn
  // finishes (or is stopped) so queued messages run one after another.
  const dispatchNext = useCallback(() => {
    const q = queuedRef.current;
    if (!q.length) return;
    const [next, ...rest] = q;
    setQueue(rest);
    setMessages((m) => [...m, { role: "user", content: next, ts: new Date().toISOString() }]);
    sendBuildRef.current(next);
  }, [setQueue]);

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
        // Type-ahead: run the next queued message, if any.
        dispatchNext();
      };
      es.addEventListener("done", finish);
      es.addEventListener("end", finish);
      es.onerror = () => {
        // CLOSED = server ended/failed (incl. a 404 for a turn that's already
        // gone). Sync from the persisted thread and unlock the composer.
        if (es.readyState === EventSource.CLOSED) void finish();
      };
    },
    [refreshThreads, scheduleAutoNameRefetch, dispatchNext],
  );

  // Render a thread's messages, reconnecting to a still-streaming last turn.
  const hydrate = useCallback(
    (t: Thread) => {
      setQueue([]);
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
    [attachStream, setQueue],
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
  }, [messages, pendingSegments, queued, busy, streaming]);

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
    setQueue([]);
    setErr(null);
    setSelectedModel("");
  }, [setQueue]);

  const stop = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    setStreaming(false);
    // Stopped watching the current turn; run anything queued behind it.
    dispatchNext();
  }, [dispatchNext]);

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

  // Keep a ref to the latest sendBuild so dispatchNext (used inside attachStream)
  // can fire a queued turn without a render cycle.
  sendBuildRef.current = sendBuild;

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text) return;
    setErr(null);
    setInput("");
    // Build turn already streaming → queue this message; it sends when the
    // current turn finishes (a session can't run two turns at once).
    if (effectiveMode === "build" && streaming) {
      setQueue([...queuedRef.current, text]);
      return;
    }
    if (effectiveMode !== "build" && busy) return;
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    if (effectiveMode === "build") sendBuild(text);
    else sendData(text);
  }, [input, effectiveMode, streaming, busy, sendBuild, sendData, setQueue]);

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

  // Recency nuance: the most recent assistant answer is expanded; older ones
  // auto-shorten. "Most recent" only applies when nothing is streaming (a live
  // turn is itself the most recent).
  const idle = !streaming && !busy && pendingSegments.length === 0;
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { lastAssistantIdx = i; break; }
  }
  const threadCfg = modelDisplay(selectedModel);

  const renderAssistant = (m: ChatMsg, isLatest: boolean) => {
    if (m.meta?.error) return m.content;
    const model = m.meta?.model ?? selectedModel;
    if (m.meta?.segments && m.meta.segments.length) {
      return <AssistantTurn segments={m.meta.segments} isLatest={isLatest} model={model} />;
    }
    return (
      <ElidedContent
        source={m.content}
        cfg={modelDisplay(model)}
        expanded={isLatest}
        render={(s) => <Markdown>{s}</Markdown>}
      />
    );
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
            {m.role === "assistant" ? (
              renderAssistant(m, idle && i === lastAssistantIdx)
            ) : (
              <ElidedContent source={m.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
            )}
          </div>
          {m.meta?.notes?.map((n, j) => (
            <div key={j} className="chat-note">{n}</div>
          ))}
        </div>
      ))}
      {(busy || streaming || pendingSegments.length > 0) && (
        <div className="chat-msg assistant">
          <div className="chat-bubble">
            {pendingSegments.length > 0 && (
              <AssistantTurn segments={pendingSegments} isLatest model={selectedModel} />
            )}
            {(streaming || busy) && <span className="chat-typing" aria-label="working">•••</span>}
          </div>
        </div>
      )}
      {queued.map((q, i) => (
        <div key={`q${i}`} className="chat-msg user">
          <div className="chat-bubble chat-bubble--queued" title="Queued — sends when the current turn finishes">{q}</div>
        </div>
      ))}
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

  const streamingBuild = effectiveMode === "build" && streaming;
  const composer = (
    <div className={`chat-composer${streamingBuild ? " is-streaming" : ""}`}>
      <textarea
        ref={inputRef}
        className="chat-input"
        rows={1}
        value={input}
        placeholder={placeholder ?? (effectiveMode === "build" ? "Tell the agent what to build..." : "Message Clawd...")}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKey}
      />
      <div className="chat-actions">
        {/* While a build turn streams, Send (queues the next message) sits to the
            left of Stop. Otherwise just Send. */}
        <button className="chat-send" onClick={send} disabled={!input.trim() || (effectiveMode !== "build" && busy)} aria-label="Send (Enter)" title={streamingBuild ? "Send — queues until the current turn finishes" : "Send (Enter)"}>
          <CornerDownLeft size={16} aria-hidden />
        </button>
        {streamingBuild && (
          <button className="chat-stop" onClick={stop} aria-label="Stop receiving (turn continues in background)" title="Stop receiving (turn continues in background)">
            <Square size={16} aria-hidden />
          </button>
        )}
      </div>
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
