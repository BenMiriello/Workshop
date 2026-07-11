import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus, Trash2, CornerDownLeft, Square, Wrench, Maximize2, ChevronLeft,
  WifiOff, ChevronRight, X, ListTodo, Clock, Slash, Mic, AudioLines, Volume2, VolumeX,
} from "lucide-react";
import { useDictation } from "../lib/useDictation";
import { useReplySpeaker } from "../lib/useReplySpeaker";
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
import { useDraft } from "../lib/draftSync";
import { UiCard, splitUiCards, hasUiCard } from "./chat/UiCard";
import { type UiCardCtx } from "./chat/uiCardActions";

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
  // Hide the toolbar's "open full screen" pop-out. Set when the host already
  // owns a single expand control for the chat (e.g. AgentBuild's region focus),
  // so there aren't two maximize buttons doing near-identical things.
  hideExpand?: boolean;
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

// Context-usage meter under the composer. The model doesn't hand back exact token
// counts, so approximate: ~4 chars/token over the visible turns + the current
// draft, against the model's context window (Claude = 200k; local models are
// smaller). It's a "how full is this chat" indicator, not an exact accounting.
const CONTEXT_WINDOW: Record<string, number> = {
  "claude-opus-4-8": 200_000,
  "claude-sonnet-4-6": 200_000,
  "claude-haiku-4-5-20251001": 200_000,
};
function contextWindowFor(model: string): number {
  if (CONTEXT_WINDOW[model]) return CONTEXT_WINDOW[model];
  if (model.startsWith("claude")) return 200_000;
  return model ? 32_768 : 200_000; // local/Ollama default; Claude fallback if unset
}
function estimateContextTokens(messages: ChatMsg[], draft: string): number {
  let chars = draft.length;
  for (const m of messages) {
    chars += (m.content || "").length;
    for (const s of m.meta?.segments || []) {
      chars += s.t === "text" ? s.text.length : s.name.length + (s.input?.length || 0);
    }
  }
  return Math.ceil(chars / 4);
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
    label = `Show ${lines.length - cfg.headLines - cfg.tailLines} more lines`;
  } else {
    headSrc = source.slice(0, cfg.headLines * CPL);
    tailSrc = source.slice(-cfg.tailLines * CPL);
    label = "Show more";
  }
  // A visible "···" divider marks the cut; the whole line expands on click.
  return (
    <>
      {render(headSrc)}
      <button type="button" className="chat-ellipsis" onClick={() => setOpen(true)}>
        <span className="chat-ellipsis__dots" aria-hidden>•••</span>
        <span className="chat-ellipsis__label">{label}</span>
      </button>
      {render(tailSrc)}
    </>
  );
}

// The answer body of an assistant turn. When it contains no ```ui-card block it
// behaves exactly as before (elided markdown). When it does, the text is split
// on the fenced blocks: surrounding markdown renders normally and each block
// renders as an interactive <UiCard/>, interleaved in order. A ui-card message
// is intentionally interactive, so its markdown runs are not elided (the card
// must stay fully visible). Works identically for a live-streamed answer and a
// stored message on reload, because both flow through here.
function AnswerBody({
  source, cfg, expanded, cardCtx,
}: {
  source: string;
  cfg: ModelDisplay;
  expanded?: boolean;
  cardCtx: UiCardCtx;
}) {
  if (!hasUiCard(source)) {
    return <ElidedContent source={source} cfg={cfg} expanded={expanded} render={(s) => <Markdown>{s}</Markdown>} />;
  }
  const parts = splitUiCards(source);
  return (
    <>
      {parts.map((p, i) =>
        p.t === "md"
          ? (p.text.trim() ? <Markdown key={i}>{p.text}</Markdown> : null)
          : <UiCard key={i} spec={p.spec} raw={p.raw} ctx={cardCtx} />,
      )}
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
function AssistantTurn({ segments, isLatest, model, cardCtx }: { segments: ChatSegment[]; isLatest: boolean; model?: string; cardCtx: UiCardCtx }) {
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
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} />
        ) : null,
      )}
    </>
  );
}

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction, scoped, hideExpand }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

  const [selectedModel, setSelectedModel] = useState<string>("");

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  // Composer text is a persisted draft (survives reload, syncs across tabs /
  // devices) keyed by this chat's contextKey — see lib/draftSync.
  const [input, setInput] = useDraft(contextKey);
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
  const [slashSel, setSlashSel] = useState(0);
  const slug = buildSlug(contextKey);

  const scrollRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const finishingRef = useRef(false);

  // Push-to-talk dictation into the composer. Interim results stream in live;
  // the final transcript is committed so a second dictation appends to it.
  const dictBaseRef = useRef("");
  const dictation = useDictation((text, partial) => {
    const base = dictBaseRef.current;
    const joined = base ? `${base} ${text}` : text;
    setInput(joined);
    if (!partial) dictBaseRef.current = joined;
  });
  const onMic = useCallback(() => {
    if (!dictation.recording) dictBaseRef.current = input.trim();
    dictation.toggle();
    inputRef.current?.focus();
  }, [dictation, input]);

  // "Speak replies" toggle: reads each freshly-arrived assistant message aloud.
  // speakNextRef is armed when we send a turn so we only speak turns we started,
  // never messages loaded from history when switching threads.
  const replySpeaker = useReplySpeaker();
  const speakNextRef = useRef(false);

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

  // Submit a concrete string as a turn (used by send() and by slash passthrough).
  const submitText = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text) return;
    setErr(null);
    // Build turn already streaming → queue this message; it sends when the
    // current turn finishes (a session can't run two turns at once).
    if (effectiveMode === "build" && streaming) {
      setQueue([...queuedRef.current, text]);
      return;
    }
    if (effectiveMode !== "build" && busy) return;
    // Arm reply-speaking for the turn we're about to start; stop any reply still
    // being read so a new question cuts off the old answer.
    speakNextRef.current = true;
    replySpeaker.stop();
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    if (effectiveMode === "build") sendBuild(text);
    else sendData(text);
  }, [effectiveMode, streaming, busy, sendBuild, sendData, setQueue, replySpeaker]);

  // Speak a freshly-arrived assistant reply when the toggle is on (both modes).
  useEffect(() => {
    if (!speakNextRef.current) return;
    const last = messages[messages.length - 1];
    if (last && last.role === "assistant" && !last.meta?.error) {
      speakNextRef.current = false;
      replySpeaker.speak(last.content);
    }
  }, [messages, replySpeaker]);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    submitText(text);
  }, [input, submitText]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Slash-command menu owns the arrow/enter/tab/escape keys while it's open.
    if (showSlash) {
      const sel = Math.min(slashSel, slashMatches.length - 1);
      if (e.key === "ArrowDown") { e.preventDefault(); setSlashSel((i) => Math.min(i + 1, slashMatches.length - 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSlashSel((i) => Math.max(i - 1, 0)); return; }
      if (e.key === "Escape") { e.preventDefault(); setInput(""); setSlashSel(0); return; }
      if (e.key === "Tab") { e.preventDefault(); setInput(`/${slashMatches[sel].name}`); return; }
      if (e.key === "Enter") { e.preventDefault(); runSlash(slashMatches[sel]); return; }
    }
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

  // ---- Slash commands ------------------------------------------------------
  // Two kinds:
  //   • "local"       — a Workshop chat action handled here (new chat, etc.).
  //   • "passthrough" — a Claude Code command (/compact, /cost, /context, …)
  //                     forwarded RAW to the agent's claude session, where the CLI
  //                     executes it natively. Build agents only (data/assistant
  //                     agents are Letta-backed, not claude sessions). Any "/x"
  //                     that isn't listed still passes through when sent, so the
  //                     full Claude command set works even though we only surface
  //                     the useful ones in the autocomplete.
  // Typing "/" opens the autocomplete; the slash button opens the same menu.
  type SlashCommand = { name: string; desc: string; available: boolean; kind: "local" | "passthrough"; run?: () => void };
  const build = effectiveMode === "build";
  const slashCommands: SlashCommand[] = [
    // Workshop chat actions.
    { name: "new", desc: "Start a new chat", available: true, kind: "local", run: () => newChat() },
    { name: "clear", desc: "Start a fresh chat (alias of new)", available: true, kind: "local", run: () => newChat() },
    { name: "delete", desc: "Delete the current chat", available: !!activeId, kind: "local", run: () => { if (activeId) setConfirmDeleteId(activeId); } },
    { name: "tasks", desc: "Toggle the task list", available: !!slug, kind: "local", run: () => setTasksOpen((o) => !o) },
    { name: "expand", desc: "Open this chat full screen", available: variant !== "full" && !hideExpand, kind: "local", run: () => popOut() },
    { name: "help", desc: "Show available commands", available: true, kind: "local", run: () => { setInput("/"); inputRef.current?.focus(); } },
    // Claude Code commands — forwarded to the agent's session (build agents only).
    { name: "compact", desc: "Summarize + compact the conversation to free up context", available: build, kind: "passthrough" },
    { name: "context", desc: "Show what's currently in the context window", available: build, kind: "passthrough" },
    { name: "cost", desc: "Show token usage and plan limits", available: build, kind: "passthrough" },
    { name: "model", desc: "Show or switch the model", available: build, kind: "passthrough" },
    { name: "memory", desc: "Show the agent's memory (CLAUDE.md)", available: build, kind: "passthrough" },
    { name: "init", desc: "Generate a CLAUDE.md for this project", available: build, kind: "passthrough" },
    { name: "review", desc: "Review a pull request", available: build, kind: "passthrough" },
    { name: "status", desc: "Show session + account status", available: build, kind: "passthrough" },
    { name: "agents", desc: "List configured subagents", available: build, kind: "passthrough" },
    { name: "mcp", desc: "Show connected MCP servers", available: build, kind: "passthrough" },
  ];
  // Visible only when the input is a single "/"-prefixed token (a command being
  // typed), filtered to available commands matching the typed prefix.
  const slashActive = input.startsWith("/") && !input.includes("\n");
  const slashQuery = slashActive ? input.slice(1).toLowerCase().trimStart().split(" ")[0] : "";
  const slashMatches = slashActive
    ? slashCommands.filter((c) => c.available && c.name.startsWith(slashQuery))
    : [];
  const showSlash = slashActive && slashMatches.length > 0;
  const runSlash = (c: SlashCommand) => {
    setInput("");
    setSlashSel(0);
    if (c.kind === "passthrough") { submitText(`/${c.name}`); return; }
    c.run?.();
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
      {variant !== "full" && !hideExpand && (
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

  // Context passed to any in-chat ui-card: navigation, the current context key,
  // the active thread id, and the rescope wrapper (Move-chat-into-project).
  const cardCtx = useMemo<UiCardCtx>(
    () => ({ navigate, contextKey, threadId: activeId, rescopeThread: chatApi.rescopeThread }),
    [navigate, contextKey, activeId],
  );

  const renderAssistant = (m: ChatMsg, isLatest: boolean) => {
    if (m.meta?.error) return m.content;
    const model = m.meta?.model ?? selectedModel;
    if (m.meta?.segments && m.meta.segments.length) {
      return <AssistantTurn segments={m.meta.segments} isLatest={isLatest} model={model} cardCtx={cardCtx} />;
    }
    return <AnswerBody source={m.content} cfg={modelDisplay(model)} expanded={isLatest} cardCtx={cardCtx} />;
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
          <div className={`chat-bubble ${m.meta?.error ? "error" : ""}${m.meta?.timeout ? " chat-bubble--timeout" : ""}`}>
            {m.role === "assistant" ? (
              <>
                {m.meta?.timeout && (
                  <div className="chat-timeout-tag">
                    <Clock size={13} strokeWidth={1.8} aria-hidden /> Turn stopped at the time limit
                  </div>
                )}
                {renderAssistant(m, idle && i === lastAssistantIdx)}
              </>
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
              <AssistantTurn segments={pendingSegments} isLatest model={selectedModel} cardCtx={cardCtx} />
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

  // Context usage below the composer. Prefer the backend's REAL count (it
  // assembles the actual prompt — persona + live app context + windowed history),
  // refetched when a turn settles; fall back to a client estimate if unavailable.
  const [serverCtx, setServerCtx] = useState<{ used: number; window: number } | null>(null);
  useEffect(() => {
    if (!activeId || busy || streaming) return;
    let cancelled = false;
    chatApi
      .threadContext(activeId)
      .then((r) => { if (!cancelled) setServerCtx({ used: r.used_tokens, window: r.window_tokens }); })
      .catch(() => { /* keep the client estimate */ });
    return () => { cancelled = true; };
  }, [activeId, messages.length, busy, streaming]);
  useEffect(() => { setServerCtx(null); }, [activeId]); // drop stale count on thread switch

  const ctx = useMemo(() => {
    if (serverCtx) {
      // Real assembled-context count + the live (unsent) draft.
      const used = serverCtx.used + Math.ceil(input.length / 4);
      return { used, win: serverCtx.window, pct: Math.min(100, Math.round((used / serverCtx.window) * 100)) };
    }
    const used = estimateContextTokens(messages, input);
    const win = contextWindowFor(selectedModel);
    return { pct: Math.min(100, Math.round((used / win) * 100)), used, win };
  }, [serverCtx, messages, input, selectedModel]);
  // Circular ring (single accent tone, no threshold colors). The progress arc is
  // a dash on a rotated circle so it fills clockwise from 12 o'clock.
  const RING_R = 7;
  const RING_C = 2 * Math.PI * RING_R;
  const contextMeter = (
    <div
      className="chat-context-meter"
      title={`~${ctx.used.toLocaleString()} of ${ctx.win.toLocaleString()} tokens used in this chat`}
    >
      <svg className="chat-context-meter__ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
        <circle className="chat-context-meter__track" cx="9" cy="9" r={RING_R} />
        <circle
          className="chat-context-meter__arc"
          cx="9" cy="9" r={RING_R}
          style={{ strokeDasharray: `${(ctx.pct / 100) * RING_C} ${RING_C}` }}
        />
      </svg>
      <span className="chat-context-meter__pct">{ctx.pct}%</span>
    </div>
  );

  const composer = (
    <div className={`chat-composer${streamingBuild ? " is-streaming" : ""}`}>
      {showSlash && (
        <ul className="chat-slash-menu" role="listbox" aria-label="Slash commands">
          {slashMatches.map((c, i) => {
            const active = i === Math.min(slashSel, slashMatches.length - 1);
            return (
              <li
                key={c.name}
                role="option"
                aria-selected={active}
                className={`chat-slash-item${active ? " is-active" : ""}`}
                onMouseEnter={() => setSlashSel(i)}
                // onMouseDown (not onClick) so it fires before the textarea blur.
                onMouseDown={(e) => { e.preventDefault(); runSlash(c); }}
              >
                <span className="chat-slash-name">/{c.name}</span>
                <span className="chat-slash-desc">{c.desc}</span>
              </li>
            );
          })}
        </ul>
      )}
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
        <button
          className="chat-slash-btn"
          onClick={() => navigate(`/voice/${encodeURIComponent(contextKey)}`)}
          aria-label="Voice mode"
          title="Voice mode — talk instead of type"
        >
          <AudioLines size={16} aria-hidden />
        </button>
        {dictation.available && (
          <button
            className={`chat-slash-btn${dictation.recording ? " is-active" : ""}`}
            onClick={onMic}
            aria-label={dictation.recording ? "Stop dictation" : "Dictate"}
            title={dictation.recording ? "Stop dictation" : "Dictate (speech to text)"}
          >
            <Mic size={16} aria-hidden />
          </button>
        )}
        {replySpeaker.available && (
          <button
            className={`chat-slash-btn${replySpeaker.on ? " is-active" : ""}`}
            onClick={replySpeaker.toggle}
            aria-label={replySpeaker.on ? "Stop speaking replies" : "Speak replies"}
            title={replySpeaker.on ? "Speaking replies aloud (tap to mute)" : "Speak replies aloud"}
          >
            {replySpeaker.on ? <Volume2 size={16} aria-hidden /> : <VolumeX size={16} aria-hidden />}
          </button>
        )}
        {!streamingBuild && (
          <button
            className={`chat-slash-btn${showSlash ? " is-active" : ""}`}
            onClick={() => { setInput(input.startsWith("/") ? "" : "/"); inputRef.current?.focus(); }}
            aria-label="Slash commands"
            title="Slash commands"
          >
            <Slash size={16} aria-hidden />
          </button>
        )}
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
      {contextMeter}
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
