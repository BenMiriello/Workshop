import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus, Trash2, CornerDownLeft, Square, Wrench, Maximize2, ChevronLeft,
  WifiOff, ChevronRight, X, ListTodo, ListPlus, Clock, Slash, AudioLines,
  PanelsTopLeft, Mic, MicOff, Loader, Settings2, AlertTriangle, PhoneOff, Play,
  UserPlus, FoldVertical, ArrowDown, MoreVertical, ChevronDown, MessagesSquare,
} from "lucide-react";
import { ConfirmDialog, StateBlock, Button, Markdown, Menu, MenuItem, MenuSeparator } from "../ui";
import { scopeLabel } from "../lib/tasks";
import TaskPanel from "./TaskPanel";
import {
  chatApi,
  type ChatMsg,
  type CardState,
  type ChatSegment,
  type Thread,
  type ThreadSummary,
  type Provider,
  shortHttpError,
} from "../lib/chat";
import { useDraft } from "../lib/draftSync";
import {
  NEW_CHAT, readSelection, writeSelection, readProvider, writeProvider,
  readQueue, writeQueue,
} from "../lib/chatSelection";
import { useVoiceSession } from "../lib/useVoiceSession";
import { unlockAudio, MIC_FAIL_TEXT } from "../lib/voice";
import VoiceSettings from "./VoiceSettings";
import { UiCard, UiCardPending, splitUiCards, hasUiCard } from "./chat/UiCard";
import { type UiCardCtx } from "./chat/uiCardActions";

// Unified Chat component. Replaces ChatPanel + BuildChatPanel.
//
// mode = "data"  → POST /threads/{id}/messages (synchronous response)
// mode = "build" → POST /threads/{id}/turns + EventSource /turns/{id}/stream
//
// variant controls chrome:
//   "dock"  - floating bottom-sheet contents
//   "panel" - inline, fills its host region (build IDE chat column)
//   "full"  - full-page, centred, fills the viewport (FocusedChat route)
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
  // Open a SPECIFIC thread on mount (e.g. resuming a draft project chat from a
  // link) instead of the most-recent one for this scope.
  initialThreadId?: string;
};

function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

function buildSlug(contextKey: string): string | null {
  return contextKey.startsWith("build:") ? contextKey.slice("build:".length) : null;
}

// The project slug this chat is bound to (if any). Used by the /deploy slash
// command to jump straight to that project's Deploys panel; general chat and
// app docks fall through to the /deploys index.
function projectSlugFor(contextKey: string): string | null {
  if (contextKey.startsWith("project:")) return contextKey.slice("project:".length);
  if (contextKey.startsWith("build:")) return contextKey.slice("build:".length);
  return null;
}

// The task-store scope a chat maps to, or null when the chat has no task queue
// (general chat, app docks). build/project/agent chats all get the shared
// TaskPanel; the contextKey IS the scope key for those.
function taskScopeFor(contextKey: string): string | null {
  if (
    contextKey.startsWith("build:") ||
    contextKey.startsWith("project:") ||
    contextKey.startsWith("agent:")
  ) {
    return contextKey;
  }
  return null;
}

// Display order for the model picker: most-reached-for first. The backend's
// list order drives default-model resolution, so this reorders the MENU only —
// anything the backend serves that isn't named here keeps its original position
// at the end, so a newly added model still shows up.
const MODEL_ORDER = [
  "claude-fable-5",
  "claude-opus-5",
  "claude-opus-4-8",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5-20251001",
];
function orderModels(ids: string[]): string[] {
  const rank = (id: string) => {
    const i = MODEL_ORDER.indexOf(id);
    return i === -1 ? MODEL_ORDER.length : i;
  };
  return [...ids].sort((a, b) => rank(a) - rank(b));
}

const MODEL_LABELS: Record<string, string> = {
  "claude-opus-5": "Opus 5",
  "claude-opus-4-8": "Opus 4.8",
  "claude-fable-5": "Fable 5",
  "claude-sonnet-5": "Sonnet 5",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};
function modelLabel(id: string): string {
  return MODEL_LABELS[id] ?? id;
}

// Why the input lane can't run — each with the step that fixes it. A generic
// "microphone error" is what made the four genuinely different causes here
// indistinguishable from each other and from a muted mic. The wording lives
// with the failure kinds in lib/voice.ts (MIC_FAIL_TEXT); it was duplicated
// here, and the two copies had drifted to different recovery hints.

// "12s" under a minute, "1m 05s" beyond - for the live working indicator.
function formatElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r.toString().padStart(2, "0")}s`;
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
// Only used for the estimate shown BEFORE a thread's first turn reports real
// usage; after that the server's measured window wins. Kept in sync with
// backend/routes/chat.py `_CONTEXT_WINDOW` — every current Claude family is 1M,
// and Haiku 4.5 is the genuine 200k exception. These were all 200k, which made
// the meter read 155% (clamped to a confident 100%) on a chat using under a
// third of its window.
const CONTEXT_WINDOW: Record<string, number> = {
  "claude-opus-5": 1_000_000,
  "claude-opus-4-8": 1_000_000,
  "claude-opus-4-7": 1_000_000,
  "claude-opus-4-6": 1_000_000,
  "claude-fable-5": 1_000_000,
  "claude-sonnet-5": 1_000_000,
  "claude-sonnet-4-6": 1_000_000,
  "claude-haiku-4-5": 200_000,
  "claude-haiku-4-5-20251001": 200_000,
};
function contextWindowFor(model: string): number {
  if (CONTEXT_WINDOW[model]) return CONTEXT_WINDOW[model];
  // An unrecognized Claude model is likelier to be newer than older, and every
  // current family except Haiku is 1M. Guessing low is the failure that pins
  // the meter at 100%; guessing high just under-reports until a turn measures.
  if (model.startsWith("claude")) return 1_000_000;
  return model ? 32_768 : 1_000_000; // local/Ollama default; Claude if unset
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
// stored message on reload, because both flow through here. While the answer
// is still streaming, a not-yet-closed ```ui-card fence renders as a skeleton
// placeholder instead of flashing the half-written JSON; once the stream ends
// (or on a stored message) an unclosed fence falls back to plain markdown.
function AnswerBody({
  source, cfg, expanded, cardCtx, streaming,
}: {
  source: string;
  cfg: ModelDisplay;
  expanded?: boolean;
  cardCtx: UiCardCtx;
  streaming?: boolean;
}) {
  if (!hasUiCard(source)) {
    return <ElidedContent source={source} cfg={cfg} expanded={expanded} render={(s) => <Markdown>{s}</Markdown>} />;
  }
  const parts = splitUiCards(source);
  return (
    <>
      {parts.map((p, i) => {
        if (p.t === "md") return p.text.trim() ? <Markdown key={i}>{p.text}</Markdown> : null;
        if (p.t === "open") {
          return streaming ? <UiCardPending key={i} /> : <Markdown key={i}>{p.text}</Markdown>;
        }
        return <UiCard key={i} spec={p.spec} raw={p.raw} ctx={cardCtx} />;
      })}
    </>
  );
}

// The "work" of an assistant turn - its tool calls + interim text. On the most
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
function AssistantTurn({ segments, isLatest, model, cardCtx, streaming }: { segments: ChatSegment[]; isLatest: boolean; model?: string; cardCtx: UiCardCtx; streaming?: boolean }) {
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
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} streaming={streaming} />
        ) : null,
      )}
    </>
  );
}

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction, scoped, hideExpand, initialThreadId }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

  const [selectedModel, setSelectedModel] = useState<string>("");

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  // How many leading messages the backend has folded into the running summary.
  // Raw messages are never dropped, so the fold is only visible if we mark it:
  // this drives the "compacted" divider in the transcript.
  const [compactedThrough, setCompactedThrough] = useState(0);
  // /compact is in flight (disables the composer's compact affordance).
  const [compacting, setCompacting] = useState(false);
  // The live turn IS the CLI's own /compact. It runs through the ordinary turn
  // machinery, so without this the indicator said "Working" — the one label that
  // tells you nothing about the several seconds you are waiting through.
  const [turnIsCompact, setTurnIsCompact] = useState(false);
  // Which in-chat cards this thread has already acted on (server-backed), so a
  // proposal that was filed and run doesn't come back offering to run again.
  const [cardStates, setCardStates] = useState<Record<string, CardState>>({});
  // Bumped after a forced compaction so the context meter refetches: folding
  // does not change messages.length, which is what otherwise drives that read.
  const [ctxNonce, setCtxNonce] = useState(0);
  // Live mirror of `messages` so effects (the voice overlay-settle poll) can read
  // the current length without listing `messages` in their dep arrays.
  const messagesRef = useRef<ChatMsg[]>(messages);
  messagesRef.current = messages;
  // Live mirror of activeId so the scope loader can read the thread we're
  // currently on WITHOUT listing activeId in its deps (which would re-run it on
  // every thread switch). It uses this to keep an open chat put instead of
  // snapping to the newest thread.
  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;
  // Composer text is a persisted draft (survives reload, syncs across tabs /
  // devices) keyed by this chat's contextKey - see lib/draftSync.
  const [input, setInput] = useDraft(contextKey);
  const [streaming, setStreaming] = useState(false);
  // Live mirror, so the queue-drain timer can re-check liveness at the moment it
  // fires rather than acting on the value captured when it was scheduled.
  const streamingRef = useRef(false);
  streamingRef.current = streaming;
  // A build turn's SSE dropped and we're re-attaching to the still-running turn
  // (proxy/idle timeouts mid-turn are normal on long turns) - the working
  // indicator stays up and says "reconnecting" instead of the turn going dead.
  const [reconnecting, setReconnecting] = useState(false);
  // Seconds elapsed on the live turn - drives the "Working… 12s" indicator.
  const [elapsed, setElapsed] = useState(0);
  // Composer focus: drives mobile keyboard chrome (drop the bottom padding so
  // there's no dead row between the buttons and the keyboard) + scroll pinning.
  const [inputFocused, setInputFocused] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);

  // Live assistant turn: ordered segments built from the SSE stream. Treat this
  // as a render-ahead cache of server state, never as the only home of a turn's
  // output: nothing may clear it until the server's copy of that turn is in
  // `messages`, or the record of what the turn did disappears from the log.
  const [pendingSegments, setPendingSegments] = useState<ChatSegment[]>([]);
  const pendingRef = useRef<ChatSegment[]>(pendingSegments);
  pendingRef.current = pendingSegments;
  // Messages the operator sent DURING the running turn and that the turn
  // accepted. `at` is how many reply segments had streamed when it landed, so
  // the log can cut the live turn at that point and show the message between
  // what came before it and what came after. They live here rather than in
  // `messages` because `messages` is rendered above the live overlay — putting
  // them there placed a later message above earlier output.
  const [interjections, setInterjections] = useState<{ at: number; text: string }[]>([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  // Starting a new chat leaves the current one — cheap to undo (the thread is
  // still there) but disorienting mid-conversation, so it asks first.
  const [confirmNew, setConfirmNew] = useState(false);

  // P4: participants on the active thread. Populated from getThread + the
  // /participants routes; canonical form is `agent:<slug>`. Empty on threads
  // that have never been assigned an agent.
  const [participants, setParticipants] = useState<string[]>([]);
  // Agents you can add to this chat. Fetched so the control can OFFER them:
  // the previous version was a bare text box asking you to type a slug, which
  // means you had to already know the answer to use it.
  const [agentChoices, setAgentChoices] = useState<{ slug: string; name: string }[]>([]);
  const [agentsErr, setAgentsErr] = useState(false);

  const addParticipant = useCallback(async (slug: string) => {
    const s = slug.trim();
    if (!activeId || !s) return;
    try {
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(activeId)}/participants`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ slug: s }),
      });
      if (r.ok) {
        const t = await r.json();
        setParticipants(t.participants ?? []);
      }
    } catch {
      /* leave participants as they are; the tag list is the source of truth */
    }
  }, [activeId]);

  // Load the agent roster once a thread is open, so the add-agent menu has real
  // names in it. A failure is remembered (not swallowed) so the menu can say it
  // couldn't load the list instead of just looking empty.
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    fetch("/api/agents/")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((list: { slug?: string; name?: string }[]) => {
        if (cancelled) return;
        setAgentChoices(
          (Array.isArray(list) ? list : [])
            .filter((a) => a.slug)
            .map((a) => ({ slug: a.slug as string, name: a.name || (a.slug as string) })),
        );
        setAgentsErr(false);
      })
      .catch(() => { if (!cancelled) setAgentsErr(true); });
    return () => { cancelled = true; };
  }, [activeId]);

  const removeParticipant = useCallback(async (p: string) => {
    if (!activeId) return;
    const slug = p.startsWith("agent:") ? p.slice(6) : p;
    const r = await fetch(`/api/chat/threads/${encodeURIComponent(activeId)}/participants/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    });
    if (r.ok) {
      const t = await r.json();
      setParticipants(t.participants ?? []);
    }
  }, [activeId]);

  // Whenever the active thread changes, pull its participants list so the
  // toolbar chips reflect the current chat rather than the previous one.
  useEffect(() => {
    if (!activeId) {
      setParticipants([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const t = await chatApi.getThread(activeId);
        if (!cancelled) setParticipants(((t as unknown) as { participants?: string[] }).participants ?? []);
      } catch {
        if (!cancelled) setParticipants([]);
      }
    })();
    return () => { cancelled = true; };
  }, [activeId]);

  // Messages typed while a build turn is streaming are queued (a session can't
  // run two turns at once) and sent automatically when the turn finishes. They
  // render as pending user bubbles below the live response.
  const [queued, setQueued] = useState<string[]>([]);
  const queuedRef = useRef<string[]>([]);
  const sendBuildRef = useRef<(t: string) => void>(() => {});

  const [taskRefetchKey, setTaskRefetchKey] = useState(0);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [openTaskCount, setOpenTaskCount] = useState(0);
  // File the chat as a tracked ticket (Stage 4). Two affordances: File ticket
  // (queue it for the dispatcher) and File and run (open a Job + launch a worker
  // now). When a run starts, the server appends a persistent job-status card to
  // the thread; we reload the messages so it shows immediately.
  const [spawning, setSpawning] = useState<false | "file" | "run">(false);
  const [spawnMsg, setSpawnMsg] = useState<
    { ok: true; text: string; scope: string; ran: boolean; jobId?: string; runError?: string }
    | { ok: false; text: string }
    | null
  >(null);
  const spawnTaskFromChat = useCallback(async (run: boolean) => {
    if (!activeId || spawning) return;
    setSpawning(run ? "run" : "file");
    setSpawnMsg(null);
    try {
      const r = await chatApi.spawnTask(activeId, { run });
      if (run && r.job_id) {
        try {
          const t = await chatApi.getThread(activeId);
          setMessages(t.messages);
        } catch { /* the card still shows on the next reload */ }
      }
      setSpawnMsg({ ok: true, text: r.task.text, scope: r.scope, ran: !!r.job_id, jobId: r.job_id, runError: r.run_error });
    } catch {
      setSpawnMsg({ ok: false, text: "Could not create a task from this chat." });
    } finally {
      setSpawning(false);
    }
  }, [activeId, spawning]);
  const [slashSel, setSlashSel] = useState(0);
  // The slash BUTTON opens the command list as a menu. It is deliberately
  // separate from "the operator is typing a /command": the button used to work
  // by writing "/" into the composer, which meant tapping it destroyed whatever
  // had been typed. A picker must never edit the thing you were writing.
  const [slashOpen, setSlashOpen] = useState(false);
  const slug = buildSlug(contextKey);
  const taskScope = taskScopeFor(contextKey);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const finishingRef = useRef(false);
  // The turn currently running, if any. A message sent while it runs is handed
  // straight to it (it lands at the next boundary between the agent's actions)
  // instead of waiting for the whole turn to end.
  const liveTurnRef = useRef<string | null>(null);
  // Whether the log is scrolled to (near) the bottom - so focus/keyboard/viewport
  // changes only re-pin when the user was already following the latest messages.
  const atBottomRef = useRef(true);
  // New content arrived while scrolled up: offer a way back instead of dragging
  // the view down.
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  // Stream resilience: count consecutive reconnect attempts (reset on any event
  // received, so a healthy long turn never trips the cap), and self-reference
  // attachStream so a drop handler can re-attach without a render cycle.
  const reconnectsRef = useRef(0);
  const turnStartRef = useRef(0);
  const attachStreamRef = useRef<(turnId: string, threadId: string, isFirst: boolean) => void>(() => {});


  // Provider list (scoped to this app, or the full cross-context list). The
  // provider drives `scope`, which is the thread list you can see — so snapping
  // back to the default on every mount stranded every chat started under the
  // other provider. Restore the one this surface was left on.
  useEffect(() => {
    let cancelled = false;
    chatApi
      .listProviders(scoped ? contextKey : undefined)
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        if (effectiveMode !== "data" || !list.length) return;
        const remembered = readProvider(contextKey);
        if (remembered && list.some((p) => p.key === remembered)) setActiveProvider(remembered);
        else if (!list.some((p) => p.key === contextKey)) setActiveProvider("general");
      })
      .catch((e) => {
        // NOT non-essential, which is what this used to claim. This list is the
        // live source for the model picker; when it fails the picker silently
        // falls back to the frontend's hardcoded MODEL_LABELS, which can offer
        // models the backend does not serve — the same frontend/backend
        // divergence that made a newly added model invisible. Say it failed
        // rather than presenting a stale list as the real one.
        if (!cancelled) setErr(`Couldn't load the model/assistant list: ${e}. The picker may be showing stale options.`);
      });
    return () => { cancelled = true; };
  }, [contextKey, effectiveMode, scoped]);

  const chooseProvider = useCallback((key: string) => {
    setActiveProvider(key);
    writeProvider(contextKey, key);
  }, [contextKey]);

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

  // ---- Voice mode -----------------------------------------------------------
  // Voice is a MODE of this chat, not a separate screen: the same thread, the
  // same message log. The engine (mic/STT/TTS/barge-in over a warm worker) drives
  // input + spoken replies; we render its in-flight turn as normal bubbles and
  // fold the finished turn into `messages` once the worker persists it (both
  // interfaces write the SAME thread - see useVoiceSession + voice_worker).
  const [voiceOn, setVoiceOn] = useState(false);
  const [voiceSettings, setVoiceSettings] = useState(false);
  // The just-finished spoken exchange, held locally so the overlay keeps showing
  // the user+assistant bubbles while the thread refetch is in flight - no frame
  // where the exchange vanishes then pops back (D4). Cleared once the refetch has
  // landed the persisted messages, so the swap into the log is a single commit.
  const [settling, setSettling] = useState<{ user: string; assistant: string } | null>(null);
  // The orb element: the mic-level halo is written here directly (a CSS var) by
  // the session's onLevel callback, off the React render path (Phase 3 item 3).
  const orbRef = useRef<HTMLButtonElement>(null);
  // The engine created a thread for a brand-new chat: adopt it as this chat's so
  // the transcript stays unified (never a parallel thread).
  const adoptThread = useCallback((id: string) => {
    if (!activeIdRef.current) {
      setActiveId(id);
      // Record it as the selection too. Adopting without persisting left a
      // voice-started chat unreachable on the next mount: the stored selection
      // still said NEW_CHAT, so the loader came back blank and the thread the
      // operator had just been talking to was only findable in the picker.
      writeSelection(scope, id);
      queueKeyRef.current = id;
    }
    void refreshThreads();
  }, [refreshThreads, scope]);
  const {
    session: vsession, snapshot: vsnap, prefs: vprefs, updatePrefs: vupdatePrefs, prewarm: vprewarm,
  } = useVoiceSession(contextKey, selectedModel || undefined, activeId ?? undefined, adoptThread);

  const toggleVoice = useCallback(() => {
    // Unlock audio HERE, in the click gesture (Phase 6.2). The lifecycle effect
    // that calls vsession.start() is NOT a user gesture, so priming there would
    // not satisfy the autoplay policy - a later held-draft release (which fires
    // outside any gesture) would still be blocked. This click is the gesture.
    unlockAudio();
    setVoiceOn((on) => !on);
  }, []);

  // Drive the engine from voiceOn (not the toggle) so that if the session object
  // is recreated mid-conversation - e.g. the model changes, which rebuilds the
  // reply path - voice re-starts on the new session instead of silently going
  // deaf. Turning off (or unmounting / switching session) stops the mic.
  useEffect(() => {
    if (!voiceOn) return;
    vprewarm();
    vsession.start();
    return () => vsession.stop();
  }, [voiceOn, vsession, vprewarm]);

  // Probe engine capabilities when voice turns on / the settings sheet opens, so
  // the provider controls reflect the truth before a session ever starts. Caps live
  // on the session (the single source): probeCaps fills them while idle, start()
  // owns them once running, and both flow to the UI via vsnap.caps.
  useEffect(() => {
    if (voiceOn || voiceSettings) void vsession.probeCaps();
  }, [voiceOn, voiceSettings, vsession]);

  // Esc interrupts the assistant mid-speech while voice is on (the settings sheet,
  // when open, owns Esc to close itself - handled by Modal).
  useEffect(() => {
    if (!voiceOn) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !voiceSettings) vsession.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [voiceOn, voiceSettings, vsession]);

  // Mic level → orb, off the React render path. The session pushes ~60fps; we
  // write it straight onto the orb element as `--vlevel` (a CSS var the halo
  // reads), so the level animates without re-rendering Chat (D4).
  useEffect(() => {
    vsession.setOnLevel((v) => {
      const el = orbRef.current;
      if (el) el.style.setProperty("--vlevel", String(0.15 + v * 0.85));
    });
    return () => vsession.setOnLevel(null);
  }, [vsession]);

  // Fatal now means one specific thing: the session has NO usable input lane
  // left (input_mode "voice" and the mic is unavailable). Anything the composer
  // can still carry — a muted mic, a denied permission while typing is allowed,
  // an insecure origin — is a standing condition shown in the strip, not a
  // reason to tear voice mode down. Muting used to land here and kill the
  // session; it no longer sets fatal at all.
  useEffect(() => {
    if (voiceOn && vsnap.fatal) {
      setErr(vsnap.error || "Voice unavailable.");
      setVoiceOn(false);
    }
  }, [voiceOn, vsnap.fatal, vsnap.error]);

  // Overlay settle (D4). When a spoken turn finishes we must never drop the frame
  // where the just-finished exchange lives: the live overlay only renders during
  // thinking|speaking, and the persisted-message refetch is async. So on finish we
  // snapshot the exchange into `settling` (kept rendered), then poll getThread
  // until the persisted turn has landed (the worker persists a beat after playback)
  // and swap `messages` + clear `settling` in ONE commit. The poll is ref-driven,
  // not tied to the effect's cleanup, so the next turn starting (a benign
  // listening→thinking transition) can't cancel an in-flight settle - turn N stays
  // on screen (as the settling overlay, above turn N+1's live overlay) until its
  // messages land.
  const settlePollRef = useRef<{ cancelled: boolean } | null>(null);
  const startSettle = useCallback((id: string, user: string, assistant: string, baseLen: number) => {
    const handle = { cancelled: false };
    settlePollRef.current = handle;
    setSettling({ user, assistant });
    let tries = 0;
    const finishSettle = (msgs: ChatMsg[] | null) => {
      if (handle.cancelled) return;
      if (msgs) setMessages(msgs);
      // Only clear if this settle is still the active one (a newer turn may have
      // taken over) - the swap into the log is a single commit either way.
      setSettling((s) => (settlePollRef.current === handle ? null : s));
      if (settlePollRef.current === handle) settlePollRef.current = null;
      void refreshThreads();
    };
    const poll = () => {
      if (handle.cancelled) return;
      chatApi.getThread(id)
        .then((t) => {
          if (handle.cancelled) return;
          if (t.messages.length > baseLen) finishSettle(t.messages);   // the new turn persisted
          else if (++tries >= 8) finishSettle(t.messages);             // give up: swap what we have
          else window.setTimeout(poll, 250);
        })
        .catch(() => {
          if (handle.cancelled) return;
          if (++tries >= 8) finishSettle(null);
          else window.setTimeout(poll, 250);
        });
    };
    poll();
  }, [refreshThreads]);

  const prevVoiceState = useRef(vsnap.state);
  // useLayoutEffect (not useEffect): the live overlay unmounts on the same commit
  // that flips state away from speaking/thinking, so `settling` must be set BEFORE
  // the browser paints, or there is a one-frame gap where the just-finished
  // exchange is absent (the vanish-then-pop this redesign set out to kill).
  useLayoutEffect(() => {
    const prev = prevVoiceState.current;
    prevVoiceState.current = vsnap.state;
    if (!voiceOn) return;
    const finished = (prev === "speaking" || prev === "thinking")
      && (vsnap.state === "listening" || vsnap.state === "idle");
    if (!finished || !activeId) return;
    const user = vsnap.lastUser;
    const assistant = vsnap.sentences.map((s) => s.text).join(" ").trim();
    if (!user && !assistant) { void refreshThreads(); return; }
    startSettle(activeId, user, assistant, messagesRef.current.length);
    // vsnap.lastUser/sentences are read at the finish transition; state is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vsnap.state, voiceOn, activeId, startSettle]);

  // Cancel any in-flight settle and drop the overlay when voice turns off, the
  // thread switches, or the component unmounts, so a stale poll can't overwrite
  // the log of another thread and the overlay can't flash back on re-entry.
  useEffect(() => {
    if (!voiceOn) {
      if (settlePollRef.current) settlePollRef.current.cancelled = true;
      setSettling(null);
    }
    return () => { if (settlePollRef.current) settlePollRef.current.cancelled = true; };
  }, [voiceOn, activeId]);

  // The queue is durable (see lib/chatSelection): it survives a refresh or a
  // closed tab, because messages sitting in it have been SENT as far as the
  // operator is concerned and dropping them is message loss.
  //
  // Which thread the queue belongs to is tracked EXPLICITLY here rather than
  // read off `activeIdRef` at write time. `activeIdRef` is assigned during
  // render, so through a thread switch it still holds the OUTGOING thread:
  // hydrate() would load the incoming thread's queue and then write it back
  // under the outgoing thread's key, erasing messages the operator had already
  // sent. Switching chats destroyed the queue of the chat you left. The key
  // moves only when the code that changes threads says it moves.
  const queueKeyRef = useRef<string>(`scope:${scope}`);
  const blankQueueKey = `scope:${scope}`;
  const setQueue = useCallback((next: string[]) => {
    queuedRef.current = next;
    setQueued(next);
    writeQueue(queueKeyRef.current, next);
  }, []);
  // Take one entry back out (the newest match). Used when something queued
  // optimistically turns out not to be needed — a no-op if the queue already
  // drained, which is the right answer: it was sent, so there is nothing to
  // retract.
  const dropQueued = useCallback((text: string) => {
    const q = queuedRef.current;
    const i = q.lastIndexOf(text);
    if (i >= 0) setQueue([...q.slice(0, i), ...q.slice(i + 1)]);
  }, [setQueue]);

  // Deliver everything queued behind the finished turn as ONE next turn. Sending
  // them one at a time made N queued messages into N sequential turns, so the
  // last thing typed could wait out several more turns before being read; the
  // operator's spec is that the next turn picks up ALL messages queued since.
  // (Not interrupting the running turn is the other half of that spec and is
  // preserved — only the Stop button ends a turn early.)
  const dispatchNext = useCallback(() => {
    const q = queuedRef.current;
    if (!q.length) return;
    setQueue([]);
    const ts = new Date().toISOString();
    setMessages((m) => [...m, ...q.map((content) => ({ role: "user" as const, content, ts }))]);
    sendBuildRef.current(q.join("\n\n"));
  }, [setQueue]);

  // A button-opened slash menu dismisses like any other popover. (The typed
  // variant closes itself when the "/" stops being a command.)
  useEffect(() => {
    if (!slashOpen) return;
    const onDown = (e: PointerEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest(".chat-slash-menu") || el.closest(".chat-slash-btn")) return;
      setSlashOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSlashOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [slashOpen]);

  // Interjections belong to ONE live turn. Once that turn is over and its
  // overlay is gone, the server transcript carries those messages, so drop the
  // local copies rather than letting them double up. Keyed off the overlay
  // rather than added to each of the eight places that reset it.
  useEffect(() => {
    if (!streaming && pendingSegments.length === 0 && interjections.length) setInterjections([]);
  }, [streaming, pendingSegments.length, interjections.length]);

  // A queue with nothing running must drain ITSELF.
  //
  // dispatchNext used to be reachable from exactly two places: the stream's
  // finish handler, and Stop. Both are "a turn just ended in THIS tab". Every
  // other way a turn can end — the page was reloaded, the tab was closed and
  // reopened, the stream dropped past its reconnect budget, the backend
  // restarted, the turn finished while the tab was asleep — left the queue
  // restored from storage on the next load with nothing that would ever send
  // it. The messages sat on screen looking pending, forever, and typing
  // something new sailed straight past them as its own turn because nothing was
  // streaming. That is a message-loss bug wearing the costume of a pending one.
  //
  // So: whenever there is something queued and no turn is running, send it.
  // Queued means the operator already hit enter; delivery is owed.
  const drainingRef = useRef(false);
  useEffect(() => {
    if (streaming || loadingThreads || !queued.length) return;
    if (drainingRef.current) return;
    drainingRef.current = true;
    // A tick of slack so a turn that is in the act of starting (sendBuild sets
    // `streaming` before its POST resolves) wins the race and we don't open a
    // second turn alongside it.
    const t = window.setTimeout(() => {
      drainingRef.current = false;
      if (!streamingRef.current && queuedRef.current.length) dispatchNext();
    }, 250);
    return () => { window.clearTimeout(t); drainingRef.current = false; };
  }, [streaming, loadingThreads, queued, dispatchNext]);

  // ---- Live SSE stream (build turns) --------------------------------------
  // Used both for a freshly-started turn and for reconnecting to an in-flight
  // turn after a reload (the server replays all events via Last-Event-ID=0).
  const attachStream = useCallback(
    (turnId: string, threadId: string, isFirst: boolean) => {
      sseRef.current?.close();
      finishingRef.current = false;
      liveTurnRef.current = turnId;
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

      // Any inbound frame means the stream is healthy: clear the reconnect
      // budget + the "reconnecting" flag, so one long turn with the odd proxy
      // drop never exhausts the cap.
      const markAlive = () => {
        reconnectsRef.current = 0;
        setReconnecting(false);
      };

      es.addEventListener("text", (e) => {
        markAlive();
        try { pushText(JSON.parse((e as MessageEvent).data).payload?.content || ""); } catch { /* ignore */ }
      });
      es.addEventListener("tool", (e) => {
        markAlive();
        try {
          const p = JSON.parse((e as MessageEvent).data).payload || {};
          setPendingSegments((s) => [...s, { t: "tool", name: p.name || "?", input: p.input || "" }]);
        } catch { /* ignore */ }
      });
      es.addEventListener("ping", markAlive); // server heartbeat (~1s) keeps this warm
      es.addEventListener("error", (e) => {
        // An in-band error EVENT (carries data) - surface its detail. This is a
        // turn-level error from the server, NOT a connection drop (that's
        // es.onerror below, which has no data).
        try {
          const d = JSON.parse((e as MessageEvent).data || "{}");
          if (d.payload?.detail) setErr(String(d.payload.detail));
        } catch { /* ignore */ }
      });

      const finish = async () => {
        if (finishingRef.current) return;
        finishingRef.current = true;
        liveTurnRef.current = null;
        es.close();
        sseRef.current = null;
        setStreaming(false);
        setReconnecting(false);
        reconnectsRef.current = 0;
        turnStartRef.current = 0;
        try {
          const t = await chatApi.getThread(threadId);
          setMessages(t.messages);
          // If the turn applied a provider action (e.g. an app persona editing
          // the budget plan), it rides in the final message's meta.notes; tell
          // the host so it refetches its data (the old sendData path did this).
          const last = t.messages[t.messages.length - 1];
          if (last?.meta?.notes?.length) onAction?.();
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

      // Connection dropped (proxy idle-timeout, network blip, backend hiccup).
      // Do NOT finalize blindly - that is what made turns look like they "just
      // stopped". Check whether the turn actually completed server-side; if it's
      // still running, re-attach (the server replays the turn's events from the
      // start), keeping the working indicator up. Give up only after
      // MAX_RECONNECTS consecutive no-progress attempts, or once the turn is
      // gone / finished.
      const MAX_RECONNECTS = 8;
      const handleDrop = async () => {
        if (finishingRef.current) return;
        es.close();
        if (sseRef.current === es) sseRef.current = null;
        try {
          const t = await chatApi.getThread(threadId);
          const last = t.messages[t.messages.length - 1];
          const stillRunning = !!(last && last.role === "assistant" && last.meta?.streaming);
          if (!stillRunning) { void finish(); return; } // turn really ended - sync + stop
        } catch { /* network error - assume the turn is still running and retry */ }
        if (reconnectsRef.current >= MAX_RECONNECTS) {
          // Out of reconnect budget on a turn the server says is STILL RUNNING.
          // Do not call finish(): that would drop to an idle-looking UI (the
          // "you told me you were working but nothing was happening" report) and
          // would dispatch queued messages into a second concurrent turn. Stop
          // retrying, keep the work on screen, say so plainly, and let the
          // liveness poll re-attach when the connection comes back.
          finishingRef.current = true;
          setStreaming(false);
          setReconnecting(false);
          setErr("Lost the live connection to this turn. It is still running on the server, reattaching...");
          return;
        }
        reconnectsRef.current += 1;
        setReconnecting(true);
        window.setTimeout(() => {
          if (!finishingRef.current) attachStreamRef.current(turnId, threadId, isFirst);
        }, 1200);
      };
      es.onerror = () => {
        // Only a CLOSED socket is terminal; CONNECTING means the browser is
        // already auto-retrying (with Last-Event-ID) - let it.
        if (es.readyState === EventSource.CLOSED) void handleDrop();
      };
    },
    [refreshThreads, scheduleAutoNameRefetch, dispatchNext, onAction],
  );
  attachStreamRef.current = attachStream;

  // Render a thread's messages, reconnecting to a still-streaming last turn.
  const hydrate = useCallback(
    (t: Thread) => {
      // Point the queue at the thread being loaded BEFORE reading it, so the
      // read-then-write round trip can't land on the thread we're leaving.
      queueKeyRef.current = t.id;
      setQueue(readQueue(t.id));
      // The fold marker is server state, not a one-shot response to clicking
      // /compact: without reading it back here the divider vanished on every
      // reload and a compacted thread looked exactly like an uncompacted one.
      setCompactedThrough(Number(t.compacted_through) || 0);
      setCardStates(t.card_states ?? {});
      const msgs = t.messages || [];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.meta?.streaming && last.meta.turn_id) {
        // Anchor the elapsed clock to the turn's REAL start (the streaming
        // message's server timestamp), so a reload mid-turn shows the true
        // elapsed time instead of restarting from zero.
        const startedMs = last.ts ? Date.parse(last.ts) : NaN;
        turnStartRef.current = Number.isFinite(startedMs) ? startedMs : Date.now();
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
        // Choose the active thread ONCE per scope, honoring (in priority order):
        //   1. the thread we're already on   → re-hydrate it, never snap to newest
        //   2. an explicit new-chat intent   → stay blank (this is what makes
        //      "New chat" / "/clear" survive a reload instead of re-adopting)
        //   3. an initialThreadId link       → resume a specific thread
        //   4. the last thread open in this scope → restore it across a refresh
        //   5. otherwise the most-recent thread
        const has = (id: string | null | undefined): boolean => !!id && list.some((x) => x.id === id);
        const stored = readSelection(scope);
        const current = activeIdRef.current;
        let target: string | null;
        if (has(current)) target = current;
        else if (stored === NEW_CHAT) target = null;
        else if (has(initialThreadId)) target = initialThreadId ?? null;
        else if (has(stored)) target = stored;
        else target = list.length ? list[0].id : null;
        if (target) {
          const t = await chatApi.getThread(target);
          if (cancelled) return;
          setActiveId(t.id);
          writeSelection(scope, t.id);
          if (t.model) setSelectedModel(t.model);
          hydrate(t);
        } else {
          setActiveId(null);
          setMessages([]);
          setPendingSegments([]);
          // Landing blank: the queue belongs to the blank-chat slot, and
          // anything already waiting there is still owed delivery.
          queueKeyRef.current = `scope:${scope}`;
          setQueued(readQueue(`scope:${scope}`));
          queuedRef.current = readQueue(`scope:${scope}`);
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

  // Liveness is SERVER state. The local `streaming` flag only knows about turns
  // THIS tab attached to, so a turn running in another tab, on another device, or
  // one whose socket died past its reconnect budget rendered as a completely idle
  // chat while work was in flight. Poll the thread while idle and re-attach the
  // moment the server says a turn is live: that restores both the truthful
  // working indicator and the live segments, from any client.
  useEffect(() => {
    if (!activeId || streaming) return;
    let cancelled = false;
    const check = async () => {
      if (cancelled || sseRef.current) return;
      try {
        const t = await chatApi.getThread(activeId);
        if (cancelled || sseRef.current) return;
        const last = t.messages[t.messages.length - 1];
        if (last?.role === "assistant" && last.meta?.streaming && last.meta.turn_id) {
          const startedMs = last.ts ? Date.parse(last.ts) : NaN;
          turnStartRef.current = Number.isFinite(startedMs) ? startedMs : Date.now();
          setErr(null);
          setMessages(t.messages.slice(0, -1));
          attachStreamRef.current(last.meta.turn_id, t.id, false);
        }
      } catch { /* transient — try again on the next tick */ }
    };
    const id = window.setInterval(check, 10_000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [activeId, streaming]);

  // Re-sync with the server when the chat comes back to the foreground. The
  // transcript can have moved on while this tab was hidden (a turn finished, or
  // the same chat was used on another device), and showing a stale log as though
  // it were current is the same "browser state does not reflect the backend"
  // problem in a different place. Skipped while a turn is live (the stream owns
  // the log) and while an unsent bubble is pending (it isn't on the server, and
  // re-syncing would silently drop it along with its retry).
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    const resync = async () => {
      if (cancelled || streaming || sseRef.current || document.hidden) return;
      if (messagesRef.current.some((m) => m.meta?.unsent)) return;
      try {
        const t = await chatApi.getThread(activeId);
        if (cancelled || streaming || sseRef.current) return;
        if (messagesRef.current.some((m) => m.meta?.unsent)) return;
        setCompactedThrough(Number(t.compacted_through) || 0);
        setCardStates(t.card_states ?? {});
        setMessages(t.messages || []);
      } catch { /* leave what's on screen */ }
    };
    const onVis = () => { if (!document.hidden) void resync(); };
    window.addEventListener("focus", resync);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", resync);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [activeId, streaming]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, []);
  // The ONLY writer of atBottomRef. Anything else writing it (the render-driven
  // effect below used to) destroys the position tracking and defeats every guard
  // that depends on it.
  const onLogScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = atBottom;
    setShowJumpToLatest((v) => (atBottom ? false : v));
  }, []);

  // Follow the conversation only while the operator is actually following it.
  // This effect fires on every streamed token; unguarded (and re-asserting
  // "we're at the bottom" as it used to) it hard-scrolled the log down on every
  // token, so scrolling up to read an earlier message while a turn streamed was
  // impossible. Leaving the bottom is not a trap: new content while scrolled up
  // raises a jump-to-latest affordance instead of yanking the view.
  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
    else if (streaming || pendingSegments.length > 0 || messages.length) setShowJumpToLatest(true);
  }, [messages, pendingSegments, interjections, queued, streaming, vsnap.state, vsnap.sentences, vsnap.lastUser, scrollToBottom]);

  // Sending is an explicit intent to follow the conversation again, so it always
  // returns to the bottom regardless of where the log was scrolled.
  const followLatest = useCallback(() => {
    atBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollToBottom();
  }, [scrollToBottom]);

  // Keep the log pinned to the bottom when the composer is focused and the mobile
  // keyboard opens/closes (which resizes the visual viewport) or the textarea
  // grows as you type. Without this the scroller keeps its pixel scrollTop while
  // the viewport shrinks, so you end up staring at older messages. Only re-pin
  // when the user was already at the bottom (atBottomRef), so scrolling up to
  // read history isn't yanked back down.
  useEffect(() => {
    if (!inputFocused) return;
    scrollToBottom();
    const vv = window.visualViewport;
    const onResize = () => { if (atBottomRef.current) scrollToBottom(); };
    vv?.addEventListener("resize", onResize);
    return () => vv?.removeEventListener("resize", onResize);
  }, [inputFocused, scrollToBottom]);

  useEffect(() => {
    if (inputFocused && atBottomRef.current) scrollToBottom();
  }, [input, inputFocused, scrollToBottom]);

  // The composer sits in flow above nothing — it SHARES the column with the log,
  // so every row the textarea grows by is a row the log loses. Re-pin whenever
  // the composer changes height, or the bottom message slides underneath it: you
  // scroll down to read the last reply, go back to typing, the composer grows,
  // and the thing you were reading is gone. Nothing else notices that resize —
  // it isn't a render, a viewport change, or a scroll — so an observer on the
  // element is what closes the gap.
  useEffect(() => {
    const el = composerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (atBottomRef.current) scrollToBottom(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [scrollToBottom]);

  // Elapsed-time ticker for the working indicator. The clock starts when a turn
  // begins streaming and keeps running across reconnects (streaming stays true),
  // so "Working… 45s" reflects the real turn duration, not the last reconnect.
  useEffect(() => {
    if (!streaming) { setElapsed(0); turnStartRef.current = 0; return; }
    if (!turnStartRef.current) turnStartRef.current = Date.now();
    const tick = () => setElapsed(Math.floor((Date.now() - turnStartRef.current) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [streaming]);

  // Auto-grow the composer: 2.5 lines tall by default, grows with content.
  const autosize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // Grow to fit content, capped at the CSS max-height (3 rows) - read it so
    // the JS cap can't drift from the stylesheet; beyond that it scrolls.
    const max = parseFloat(getComputedStyle(el).maxHeight) || 96;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, []);
  // useLayoutEffect, not useEffect: the scroll-to-bottom effects above also fire
  // on `input`, and passive effects run in declaration order — so resizing here
  // passively meant every scroll was computed against the composer's PREVIOUS
  // height and landed short by exactly the row that had just been added. Layout
  // effects run first regardless of order, so the measurement is never stale.
  useLayoutEffect(() => { autosize(); }, [input, autosize]);

  // (There is no in-chat "open another thread" path any more. Switching
  // conversations goes through the chat list, which navigates here with
  // ?thread=<id>; the mount effect above adopts it and persists the selection.)

  const newChat = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    setActiveId(null);
    setMessages([]);
    setPendingSegments([]);
    // Move the queue key to the blank-chat slot FIRST. Otherwise clearing the
    // composer queue for the new chat would wipe the queue still owed to the
    // thread being left behind.
    queueKeyRef.current = blankQueueKey;
    setQueue([]);
    setErr(null);
    setSelectedModel("");
    setCompactedThrough(0);
    setCardStates({});
    // Persist the intent so a reload / navigation lands on a blank new chat
    // rather than silently re-adopting the most-recent thread.
    writeSelection(scope, NEW_CHAT);
  }, [setQueue, scope, blankQueueKey]);

  const stop = useCallback(() => {
    sseRef.current?.close();
    sseRef.current = null;
    liveTurnRef.current = null;
    // Claim the finish so a pending drop handler can't re-attach behind us.
    finishingRef.current = true;
    setStreaming(false);
    setReconnecting(false);
    // Reconcile BEFORE handing off to the next turn. Stopping used to drop
    // straight into dispatchNext, and the next turn's attachStream then cleared
    // pendingSegments — so everything the stopped turn had already done vanished
    // from the transcript. Pull the server's copy first; if that fetch fails,
    // fold what's on screen into the log locally rather than losing it.
    const id = activeIdRef.current;
    if (!id) { dispatchNext(); return; }
    chatApi.getThread(id)
      .then((t) => { setMessages(t.messages); setPendingSegments([]); })
      .catch(() => {
        const segs = pendingRef.current;
        if (segs.length) {
          setMessages((m) => [...m, {
            role: "assistant", content: "", ts: new Date().toISOString(), meta: { segments: segs },
          }]);
        }
        setPendingSegments([]);
      })
      .finally(() => dispatchNext());
  }, [dispatchNext]);

  const removeThread = useCallback(async (id: string) => {
    await chatApi.deleteThread(id);
    if (id === activeId) newChat();
    refreshThreads();
  }, [activeId, newChat, refreshThreads]);

  // The universal turn sender. EVERY chat (general, app, project, build) streams
  // through POST /threads/{id}/turns + the SSE channel, so every turn shows its
  // work (tool calls, interim text) live instead of a blocking spinner. The
  // backend picks the tool surface + session from the provider class; the client
  // is transport-uniform.
  const sendBuild = useCallback(async (text: string) => {
    setStreaming(true);
    setTurnIsCompact(text.trim() === "/compact");
    setPendingSegments([]);
    turnStartRef.current = Date.now(); // fresh turn - anchor the elapsed clock now
    const isFirst = messages.length <= 1;
    try {
      let id = activeId;
      if (!id) {
        const t = await chatApi.createThread(scope);
        id = t.id;
        setActiveId(id);
        // Put the new thread in the picker immediately and record it as the
        // selection. Without this a chat you just started was unreachable the
        // moment you navigated away: it wasn't in the local list, and nothing
        // remembered you were in it.
        setThreads((list) => (list.some((x) => x.id === t.id) ? list : [t, ...list]));
        writeSelection(scope, t.id);
        // Carry the type-ahead queue across the moment a blank chat becomes a
        // real thread. Anything typed while this first turn was starting was
        // stored under the blank-chat key; without moving it, the next hydrate
        // reads the (empty) thread queue and those messages are never delivered
        // and never seen again.
        const carried = queuedRef.current;
        queueKeyRef.current = t.id;
        writeQueue(blankQueueKey, []);
        if (carried.length) writeQueue(t.id, carried);
      }
      const r = await fetch(`/api/chat/threads/${id}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selectedModel ? { content: text, model: selectedModel } : { content: text }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const { turn_id } = await r.json();
      attachStream(turn_id, id, isFirst);
    } catch (e) {
      setErr(String(e));
      setStreaming(false);
      // The turn never started, so the server has no copy of this message. Mark
      // the optimistic bubble instead of leaving it looking delivered: a bubble
      // that silently isn't real is exactly what "my messages keep disappearing"
      // felt like from the outside.
      setMessages((m) => {
        const i = m.length - 1;
        if (i < 0 || m[i].role !== "user" || m[i].content !== text) return m;
        const copy = m.slice();
        copy[i] = { ...copy[i], meta: { ...copy[i].meta, unsent: true } };
        return copy;
      });
    }
  }, [activeId, scope, selectedModel, messages.length, attachStream, blankQueueKey]);

  // Re-send a message that never reached the server (drops the marked bubble and
  // runs it as a fresh turn).
  const retryUnsent = useCallback((idx: number, text: string) => {
    setMessages((m) => m.filter((_, i) => i !== idx));
    setErr(null);
    void sendBuildRef.current(text);
    setMessages((m) => [...m, { role: "user", content: text, ts: new Date().toISOString() }]);
  }, []);

  // Keep a ref to the latest sendBuild so dispatchNext (used inside attachStream)
  // can fire a queued turn without a render cycle.
  sendBuildRef.current = sendBuild;

  // /compact — reclaim context now. A chat's context lives in TWO places and
  // this has to reach both, which is why compacting one of them alone never
  // visibly did anything:
  //   1. the stored transcript, re-assembled whenever a fresh session starts →
  //      folded server-side by POST /threads/:id/compact.
  //   2. the live claude session, whose window is what actually fills up → only
  //      the CLI's own /compact frees that, and it does so by running as a turn.
  // Running (2) as a normal turn is deliberate: the fold then appears in the
  // transcript with a reply, instead of being an invisible state change.
  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr("Nothing to compact — this chat has no messages yet."); return; }
    // Deliberately NOT gated on `streaming`. Folding the stored transcript is a
    // server-side operation on the saved thread and is independent of whatever
    // turn is in flight; refusing while a turn ran meant /compact was rejected
    // almost every time it was reached for, since that is exactly when context
    // is under pressure. The live-session half is what has to wait, and it
    // queues below rather than being dropped.
    setErr(null);
    setCompacting(true);
    // Show the request in the log NOW, in the place it happened.
    //
    // The live-session half has to run as its own turn, so behind a running turn
    // it queues. It used to queue only AFTER the server fold returned — several
    // seconds of round trip, during which the running turn kept streaming — so
    // the "/compact" bubble landed below a wall of output that arrived after it
    // was asked for, and read as though the request had been ignored and then
    // picked up at random. Queue it up front and it sits in true order.
    const preQueued = streamingRef.current && effectiveMode === "build";
    if (preQueued) setQueue([...queuedRef.current, "/compact"]);
    // Compaction is a several-second server round trip. Without a note here the
    // only feedback was the finished result, so a slow (or failed) pass was
    // indistinguishable from a button that did nothing.
    setSpawnMsg({
      ok: true,
      text: preQueued
        ? "Compacting. The live session folds when the running turn finishes."
        : "Compacting the conversation…",
      scope: scope,
      ran: false,
    });
    try {
      const r = await chatApi.compactThread(id);
      setCompactedThrough(r.compacted_through);
      // The context meter is derived server-side from the compacted prompt, so
      // re-read it rather than guessing at the new number.
      setCtxNonce((n) => n + 1);
      if (r.live_session) {
        // The CLI's own /compact is the only thing that frees the live window,
        // and it runs as a normal turn — so behind a running turn it queues
        // instead of opening a second one. Already queued above in that case.
        if (!preQueued) {
          if (streamingRef.current) {
            setQueue([...queuedRef.current, "/compact"]);
          } else {
            setMessages((m) => [...m, { role: "user", content: "/compact", ts: new Date().toISOString() }]);
            void sendBuildRef.current("/compact");
          }
          setSpawnMsg(null);
        }
      } else {
        // No live session after all — take the speculative entry back out rather
        // than letting a literal "/compact" reach a model as text.
        if (preQueued) dropQueued("/compact");
        if (r.folded === 0) {
          setSpawnMsg(null);
          setErr("Nothing to compact — the transcript is already short.");
        } else {
          setSpawnMsg({ ok: true, text: `Compacted ${r.folded} messages into the running summary.`, scope: scope, ran: false });
        }
      }
    } catch (e) {
      if (preQueued) dropQueued("/compact");
      setSpawnMsg(null);
      setErr(`Couldn't compact: ${String(e)}`);
    } finally {
      setCompacting(false);
    }
  }, [setQueue, dropQueued, scope, effectiveMode]);

  // Submit a concrete string as a turn (used by send() and by slash passthrough).
  const submitText = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text) return;
    setErr(null);
    followLatest();
    // /compact is a Workshop action, not a message. Intercept it here as well as
    // in the autocomplete, so typing it and hitting enter folds the transcript
    // instead of sending the literal string "/compact" to the model.
    //
    // This has to come BEFORE the queue branch. Queued behind a running turn it
    // was treated as ordinary text and drained into the next turn joined to
    // whatever else was waiting — so it neither compacted nor read as a message.
    // compact() refuses politely while a turn is live, which is the honest
    // answer rather than a silent no-op.
    if (text === "/compact") { void compact(); return; }
    // A turn is already running → hand the message TO it. The agent reads
    // pending input at the next boundary between its actions, so it arrives
    // within a step or two instead of after the whole turn. It does not
    // interrupt the work in flight; only Stop does that.
    //
    // Queueing is now only the fallback for the narrow case where the turn ends
    // between hitting enter and the request landing. The message is never lost:
    // either the running turn took it, or it becomes the next turn.
    // Addressed by CHAT, not by turn id. Gating this on a turn id held in a ref
    // meant injection only fired in the component mount that started the turn:
    // a second tab, another device, a turn adopted after a backend restart, or
    // a reload that re-attached by another path all had no id to send, so the
    // message silently went back to the queue and waited out the whole turn —
    // the exact behaviour this was supposed to end. The client knows which
    // conversation it is in; the server resolves which turn is live on it.
    const threadId = activeIdRef.current;
    if (streaming && threadId) {
      void chatApi.injectIntoThread(threadId, text)
        .then((r) => {
          if (r.delivered) {
            // NOT into `messages` — that list renders ABOVE the live turn's
            // overlay, so a message sent DURING the turn jumped above output
            // that was already on screen before it. Record it as an
            // interjection pinned to how much of the reply had streamed when it
            // landed; the log splits the live turn there and renders it in
            // real order (see `log`).
            setInterjections((x) => [...x, { at: pendingRef.current.length, text }]);
            followLatest();
          } else {
            setQueue([...queuedRef.current, text]);
          }
        })
        .catch(() => { setQueue([...queuedRef.current, text]); });
      return;
    }
    if (streaming) { setQueue([...queuedRef.current, text]); return; }
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    sendBuild(text);
  }, [streaming, sendBuild, setQueue, compact, followLatest]);

  // Composer text change. In voice mode, keep the engine's typing-hold aware of
  // whether the composer currently holds text (so it defers taking its turn).
  const onComposerChange = useCallback((v: string) => {
    setInput(v);
    if (voiceOn) vsession.setTyping(v.trim().length > 0);
  }, [setInput, voiceOn, vsession]);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    // In voice mode a typed message runs through the voice engine too (so the
    // reply is spoken and uses the warm worker); slash-commands still go the
    // normal chat route.
    if (voiceOn && !text.startsWith("/")) {
      vsession.submitText(text);
      vsession.setTyping(false);
    } else {
      submitText(text);
    }
  }, [input, submitText, voiceOn, vsession, setInput]);

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
    // Prefer where you actually came from; otherwise the chat list, which is
    // this surface's parent now that the thread dropdown is gone.
    if (window.history.length > 1) navigate(-1);
    else navigate("/chats");
  };

  // ---- Slash commands ------------------------------------------------------
  // Two kinds:
  //   • "local"       - a Workshop chat action handled here (new chat, etc.).
  //   • "passthrough" - a Claude Code command (/compact, /cost, /context, …)
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
    { name: "tasks", desc: "Toggle the task list", available: !!taskScope, kind: "local", run: () => setTasksOpen((o) => !o) },
    { name: "expand", desc: "Open this chat full screen", available: variant !== "full" && !hideExpand, kind: "local", run: () => popOut() },
    { name: "help", desc: "Show available commands", available: true, kind: "local", run: () => setSlashOpen(true) },
    // /deploy — jump to the project's Deploys panel when the chat is bound to
    // one, else to the cross-project /deploys index. The actual "kick a deploy"
    // action still lives at those pages; the command is a keyboard shortcut.
    { name: "deploy", desc: "Open deploys for this project (or the index)", available: true, kind: "local", run: () => {
        const slug = projectSlugFor(contextKey);
        if (slug) navigate(`/projects/${encodeURIComponent(slug)}#deploys`);
        else navigate(`/deploys`);
      } },
    // /compact is a Workshop action in EVERY mode, not a Claude Code passthrough:
    // it folds this thread's transcript server-side via POST /threads/:id/compact.
    { name: "compact", desc: "Summarize + compact the conversation to free up context", available: !compacting, kind: "local", run: () => void compact() },
    // Claude Code commands - forwarded to the agent's session (build agents only).
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
  const slashTyped = input.startsWith("/") && !input.includes("\n");
  const slashQuery = slashTyped ? input.slice(1).toLowerCase().trimStart().split(" ")[0] : "";
  // Typed: filter by what's been typed. Button-opened: the whole list, because
  // nothing was typed to filter by.
  const slashMatches = slashTyped
    ? slashCommands.filter((c) => c.available && c.name.startsWith(slashQuery))
    : slashOpen
      ? slashCommands.filter((c) => c.available)
      : [];
  const showSlash = (slashTyped || slashOpen) && slashMatches.length > 0;
  const runSlash = (c: SlashCommand) => {
    // Only clear the composer when its contents WERE the command being typed.
    // Picking from the button-opened menu leaves your draft exactly as it was.
    if (slashTyped) setInput("");
    setSlashOpen(false);
    setSlashSel(0);
    if (c.kind === "passthrough") { submitText(`/${c.name}`); return; }
    c.run?.();
  };

  const inFlight = streaming;

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

  // ---- Header: scope + where-you-are on the left, actions on the right ----
  // The conversation picker used to live here as a third <select>. It is gone:
  // it duplicated the chat list you reach with back / "All chats", and its one
  // unique job (start a new chat) is now the + button.
  const activeThreadTitle = activeId ? threads.find((t) => t.id === activeId)?.title : undefined;
  // Agents not already in this chat — the only ones worth offering.
  const inChat = new Set(participants.map((p) => (p.startsWith("agent:") ? p.slice(6) : p)));
  const available = agentChoices.filter((a) => !inChat.has(a.slug));
  const toolbar = (
    <div className="chat-toolbar">
      {variant === "full" && (
        <button className="chat-tb__btn" onClick={back} title="Back" aria-label="Back">
          <ChevronLeft size={16} aria-hidden />
        </button>
      )}
      {/* Build agents: from the focused chat, open the full workbench (files /
          editor / preview / diff). Only in the full chat - inside the workbench
          itself the chat is already embedded, so the button would be redundant. */}
      {effectiveMode === "build" && slug && variant === "full" && (
        <button
          className="chat-tb__btn"
          onClick={() => navigate(`/agents/${slug}/chat`)}
          title="Open workbench (files · editor · preview · diff)"
          aria-label="Open workbench"
        >
          <PanelsTopLeft size={16} aria-hidden />
        </button>
      )}
      {/* Scope picker: which assistant this conversation is with. This is the
          ONE picker that belongs in the header — it decides what the chat can
          see and which thread list you're in, so changing it changes the whole
          surface. Model moved down to the composer (a per-turn knob), and the
          conversation picker is gone entirely: back / "All chats" reaches the
          list, and + starts a new one. */}
      {showProviderPicker && providers.length > 1 && effectiveMode === "data" ? (
        <Menu
          label="Assistant"
          align="start"
          trigger={
            <button className="chat-tb__picker" title="Which assistant to talk to">
              <span className="chat-tb__picker-val">{activeProviderObj?.label ?? activeProvider}</span>
              <ChevronDown size={14} aria-hidden className="chat-tb__picker-caret" />
            </button>
          }
        >
          {providers.map((p) => (
            <MenuItem key={p.key} checked={p.key === activeProvider} onSelect={() => chooseProvider(p.key)}>
              {p.label}
            </MenuItem>
          ))}
        </Menu>
      ) : showProviderPicker && providers.length === 1 && effectiveMode === "data" ? (
        <span className="chat-tb__picker is-static">{providers[0].label}</span>
      ) : null}
      {/* Which conversation you're in — a label, not a control. */}
      {activeThreadTitle && <span className="chat-tb__title">{activeThreadTitle}</span>}
      {/* Spacer pushes the per-thread action buttons to the right on desktop; on
          mobile the toolbar wraps and the actions flow onto the next line. */}
      <span className="chat-tb__spacer" />
      {/* P4: participants control. Renders each agent currently in this chat
          as a small removable tag, plus a compact "Add" input to invite
          another agent by slug. An agent in a chat is a property of the
          chat, not its scope. */}
      {activeId && (
        <>
          {participants.map((p) => {
            const slug = p.startsWith("agent:") ? p.slice(6) : p;
            return (
              <button
                key={p}
                className="chat-tb__btn chat-tb__btn--wide"
                title={`Remove ${slug} from this chat`}
                aria-label={`Remove participant ${slug}`}
                onClick={() => void removeParticipant(p)}
              >
                <span className="chat-tb__btn-text">{slug}</span>
                <X size={12} aria-hidden />
              </button>
            );
          })}
          {/* Pick an agent from the ones that exist. Asking the operator to type
              a slug into a blank box required already knowing the answer. */}
          <Menu
            label="Add an agent to this chat"
            align="end"
            trigger={
              <button className="chat-tb__btn" title="Add an agent to this chat" aria-label="Add agent">
                <UserPlus size={16} aria-hidden />
              </button>
            }
          >
            {agentsErr ? (
              <MenuItem onSelect={() => navigate("/agents")}>Couldn't load agents — open Agents</MenuItem>
            ) : available.length === 0 ? (
              <MenuItem onSelect={() => navigate("/agents")}>
                {agentChoices.length ? "All agents are already here" : "No agents yet — create one"}
              </MenuItem>
            ) : (
              available.map((a) => (
                <MenuItem key={a.slug} icon={<UserPlus size={14} />} onSelect={() => void addParticipant(a.slug)}>
                  {a.name}
                </MenuItem>
              ))
            )}
          </Menu>
        </>
      )}
      {/* File-as-ticket and file-and-run stay VISIBLE. They were moved into the
          overflow in the header rework; that was not asked for — these two were
          flagged for discussion, not relocation — so they are back on the row. */}
      {activeId && (
        <button
          className="chat-tb__btn"
          onClick={() => { if (spawning === false) void spawnTaskFromChat(false); }}
          title="File this chat as a tracked ticket"
          aria-label="File as a ticket"
          aria-busy={spawning === "file"}
        >
          {spawning === "file" ? <Loader size={16} aria-hidden className="ws-spin" /> : <ListPlus size={16} aria-hidden />}
        </button>
      )}
      {activeId && (
        <button
          className="chat-tb__btn"
          onClick={() => { if (spawning === false) void spawnTaskFromChat(true); }}
          title="File this chat as a ticket and run it now"
          aria-label="File as a ticket and run it now"
          aria-busy={spawning === "run"}
        >
          {spawning === "run" ? <Loader size={16} aria-hidden className="ws-spin" /> : <Play size={16} aria-hidden />}
        </button>
      )}
      {taskScope && (
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
      <button
        className="chat-tb__btn"
        onClick={() => setConfirmNew(true)}
        title="Start a new chat"
        aria-label="New chat"
      >
        <Plus size={16} aria-hidden />
      </button>
      {/* Overflow: only the things that are genuinely rare or destructive —
          finding another chat, going full screen, deleting this one. Actions
          you take on a conversation stay on the row where you can see them. */}
      <Menu
        label="Chat actions"
        align="end"
        trigger={
          <button className="chat-tb__btn" title="More" aria-label="More chat actions">
            <MoreVertical size={16} aria-hidden />
          </button>
        }
      >
        <MenuItem icon={<MessagesSquare size={14} />} onSelect={() => navigate("/chats")}>
          All chats
        </MenuItem>
        {variant !== "full" && !hideExpand && (
          <MenuItem icon={<Maximize2 size={14} />} onSelect={popOut}>
            Open full screen
          </MenuItem>
        )}
        {activeId && (
          <>
            <MenuSeparator />
            <MenuItem danger icon={<Trash2 size={14} />} onSelect={() => setConfirmDeleteId(activeId)}>
              Delete chat…
            </MenuItem>
          </>
        )}
      </Menu>
    </div>
  );

  const confirmDeleteTitle = confirmDeleteId
    ? threads.find((t) => t.id === confirmDeleteId)?.title
    : undefined;

  const newChatDialog = (
    <ConfirmDialog
      open={confirmNew}
      onCancel={() => setConfirmNew(false)}
      onConfirm={() => { setConfirmNew(false); newChat(); }}
      title="Start a new chat?"
      body={
        activeId ? (
          <span>
            This leaves {activeThreadTitle ? `"${activeThreadTitle}"` : "the current chat"} and opens a blank
            one in {scopeLabel(scope)}. Nothing is deleted — you can come back to it from All chats.
          </span>
        ) : (
          <span>Opens a blank chat in {scopeLabel(scope)}.</span>
        )
      }
      confirmLabel="New chat"
      danger={false}
    />
  );

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
  const idle = !streaming && pendingSegments.length === 0;
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { lastAssistantIdx = i; break; }
  }
  const threadCfg = modelDisplay(selectedModel);

  // Context passed to any in-chat ui-card: navigation, the current context key,
  // the active thread id, the rescope wrapper (Move-chat-into-project), and the
  // server-backed record of which cards have already been acted on.
  const recordCardState = useCallback(
    (key: string, state: { phase: string; note?: string | null; slug?: string | null; title?: string | null }) => {
      setCardStates((prev) => ({ ...prev, [key]: state }));
      const id = activeIdRef.current;
      if (!id) return;
      void chatApi.setCardState(id, key, state)
        // The server's map is the authority, not the optimistic patch. Taking
        // the response back is what keeps a card that has been acted on from
        // re-offering "File and run" after a reload.
        .then((r) => { if (r?.card_states) setCardStates(r.card_states); })
        .catch((e) => {
          // Previously swallowed with a note that it would "re-sync on the next
          // action" — it never did. The optimistic state simply diverged: the
          // card looked filed, the server had no record, and the next hydrate
          // brought back an un-acted card for work that had already run. Say
          // so instead, so the state on screen and the state on disk agree.
          setErr(`Couldn't record that action on the server: ${e}. Reload to see the real state of this card.`);
        });
    },
    [],
  );
  const cardCtx = useMemo<UiCardCtx>(
    () => ({
      navigate, contextKey, threadId: activeId, rescopeThread: chatApi.rescopeThread,
      cardStates, recordCardState,
    }),
    [navigate, contextKey, activeId, cardStates, recordCardState],
  );

  const renderAssistant = (m: ChatMsg, isLatest: boolean) => {
    if (m.meta?.error) return m.content;
    const model = m.meta?.model ?? selectedModel;
    if (m.meta?.segments && m.meta.segments.length) {
      return <AssistantTurn segments={m.meta.segments} isLatest={isLatest} model={model} cardCtx={cardCtx} />;
    }
    return <AnswerBody source={m.content} cfg={modelDisplay(model)} expanded={isLatest} cardCtx={cardCtx} />;
  };

  const jumpToLatest = showJumpToLatest ? (
    <div className="chat-jump">
      <Button size="sm" variant="ghost" onClick={followLatest}>
        <ArrowDown size={14} aria-hidden /> Jump to latest
      </Button>
    </div>
  ) : null;

  // The live turn, cut at every point the operator interjected. With no
  // interjections (the common case) this is exactly one assistant bubble
  // carrying the streamed segments plus the working indicator — unchanged. With
  // them, the reply is split so each message sits between the output that
  // preceded it and the output that followed, which is the order things
  // actually happened in.
  const liveTurnBlocks = (() => {
    const cuts = interjections.map((x) => Math.min(x.at, pendingSegments.length));
    const bounds = [0, ...cuts, pendingSegments.length];
    const blocks: ReactNode[] = [];
    for (let k = 0; k < bounds.length - 1; k++) {
      const slice = pendingSegments.slice(bounds[k], bounds[k + 1]);
      const isLastSlice = k === bounds.length - 2;
      // The working indicator belongs to the tail of the reply, so it renders
      // below the newest interjection rather than stranded above it.
      const showWorking = streaming && isLastSlice;
      if (slice.length || showWorking) {
        blocks.push(
          <div className="chat-msg assistant" key={`live-a${k}`}>
            <div className="chat-bubble">
              {slice.length > 0 && (
                <AssistantTurn
                  segments={slice}
                  isLatest={isLastSlice}
                  model={selectedModel}
                  cardCtx={cardCtx}
                  streaming
                />
              )}
              {showWorking && (
                <div className="chat-working" role="status" aria-live="polite">
                  <span className="chat-working__label">
                    {reconnecting ? "Reconnecting" : turnIsCompact ? "Compacting" : "Working"}
                  </span>
                  <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
                  {elapsed > 0 && <span className="chat-working__time">{formatElapsed(elapsed)}</span>}
                </div>
              )}
            </div>
          </div>,
        );
      }
      if (k < interjections.length) {
        blocks.push(
          <div className="chat-msg user" key={`live-ij${k}`}>
            <div className="chat-bubble" title="Sent mid-turn; the running turn picked it up">
              {interjections[k].text}
            </div>
          </div>,
        );
      }
    }
    return <>{blocks}</>;
  })();

  const log = (
    <div className="chat-log" ref={scrollRef} onScroll={onLogScroll}>
      {loadingThreads && messages.length === 0 ? (
        <div className="chat-empty">Loading...</div>
      ) : err && messages.length === 0 ? (
        <div className="chat-empty chat-empty--err">Couldn't load threads. {String(err).slice(0, 240)}</div>
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
        <Fragment key={i}>
        {/* The fold marker. Compaction never drops raw messages, so without this
            divider a compacted thread looks identical to an uncompacted one and
            the operator can't tell whether /compact did anything. */}
        {compactedThrough > 0 && i === compactedThrough && (
          <div className="chat-fold" role="separator">
            <FoldVertical size={14} strokeWidth={1.8} aria-hidden />
            <span>
              {compactedThrough} earlier {compactedThrough === 1 ? "message" : "messages"} folded into the running summary
            </span>
          </div>
        )}
        <div className={`chat-msg ${m.role}`}>
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
              <>
                <ElidedContent source={m.content} cfg={threadCfg} expanded={false} render={(s) => <>{s}</>} />
                {m.meta?.unsent && (
                  <div className="chat-unsent">
                    <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
                    <span>Not sent, this message never reached the server.</span>
                    <Button size="sm" variant="ghost" onClick={() => retryUnsent(i, m.content)}>Retry</Button>
                  </div>
                )}
              </>
            )}
          </div>
          {m.meta?.notes?.map((n, j) => (
            <div key={j} className="chat-note">{n}</div>
          ))}
        </div>
        </Fragment>
      ))}
      {(streaming || pendingSegments.length > 0) && liveTurnBlocks}
      {queued.map((q, i) => (
        <div key={`q${i}`} className="chat-msg user">
          <div className="chat-bubble chat-bubble--queued" title="Queued, sends when the current turn finishes">{q}</div>
        </div>
      ))}
      {/* Live spoken turn: the just-sent utterance + the streaming reply, rendered
          as normal bubbles. The heard / speaking / pending caption cue is the one
          voice-specific touch - it lives INSIDE a standard assistant bubble. When
          the turn finishes it does NOT vanish: it's held in `settling` (plain
          bubbles) until the refetch lands the persisted messages, then swaps once
          into the log (D4 - no vanish-then-pop). Muted (captions-only) replies
          stay in `thinking` the whole time and stream their captions here too. */}
      {/* The settling exchange (the just-finished turn) renders ABOVE the live
          overlay so a fast follow-up turn never hides it: turn N stays put as
          plain bubbles until its persisted messages land, then swaps out. */}
      {voiceOn && settling && (
        <>
          {settling.user && (
            <div className="chat-msg user">
              <div className="chat-bubble">{settling.user}</div>
            </div>
          )}
          {settling.assistant && (
            <div className="chat-msg assistant">
              <div className="chat-bubble">{settling.assistant}</div>
            </div>
          )}
        </>
      )}
      {voiceOn && (vsnap.state === "thinking" || vsnap.state === "speaking") && (
        <>
          {vsnap.lastUser && (
            <div className="chat-msg user">
              <div className="chat-bubble">{vsnap.lastUser}</div>
            </div>
          )}
          <div className="chat-msg assistant">
            <div className="chat-bubble">
              {vsnap.sentences.length > 0 ? (
                <span className="chat-voice-live">
                  {vsnap.sentences.map((s, i) => (
                    <span
                      key={i}
                      className={
                        s.status === "heard" ? "chat-voice-heard"
                        : s.status === "speaking" ? "chat-voice-now"
                        : "chat-voice-pending"
                      }
                    >
                      {s.text}{" "}
                    </span>
                  ))}
                </span>
              ) : (
                <div className="chat-working" role="status" aria-live="polite">
                  <span className="chat-working__label">Thinking</span>
                  <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );

  const taskPanel = taskScope ? (
    <TaskPanel
      scope={taskScope}
      refetchKey={taskRefetchKey}
      open={tasksOpen}
      onCount={setOpenTaskCount}
      variant="drawer"
    />
  ) : null;

  // Every chat streams now, so the streaming affordances (Stop, queue-next hint,
  // composer streaming state) apply whenever a turn is live, regardless of mode.
  const streamingBuild = streaming;

  // Context usage below the composer. Prefer the backend's REAL count (it
  // assembles the actual prompt - persona + live app context + windowed history),
  // refetched when a turn settles; fall back to a client estimate if unavailable.
  const [serverCtx, setServerCtx] = useState<{ used: number; window: number } | null>(null);
  useEffect(() => {
    if (!activeId || streaming) return;
    let cancelled = false;
    chatApi
      .threadContext(activeId)
      .then((r) => { if (!cancelled) setServerCtx({ used: r.used_tokens, window: r.window_tokens }); })
      .catch(() => { /* keep the client estimate */ });
    return () => { cancelled = true; };
  }, [activeId, messages.length, streaming, ctxNonce]);
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

  // Voice affordances for the composer: the state "orb" doubles as interrupt (when
  // speaking) / send-now (when listening). The orb NEVER shows a Mic glyph - that
  // was the confusing twin of the mic-mute button (D6). It uses AudioLines
  // (listening, with the level halo), Loader (thinking/connecting), Square
  // (speaking, the stop affordance), and AlertTriangle (error/idle-unexpectedly).
  const voiceErrored = !!vsnap.error;
  const voiceOrbAction = () => {
    if (vsnap.state === "speaking") vsession.interrupt();
    else if (vsnap.state === "thinking") vsession.cancelTurn();  // scrap a mis-heard send
    else if (vsnap.state === "listening") vsession.forceEndUtterance();
  };
  const voiceOrbIcon =
    voiceErrored || vsnap.state === "idle"
      ? <AlertTriangle size={16} aria-hidden />
      : vsnap.state === "connecting"
        ? <Loader size={16} aria-hidden className="ws-spin" />
        : vsnap.state === "thinking"
          ? <X size={16} aria-hidden />          /* tap = scrap the mis-heard turn */
          : vsnap.state === "speaking"
            ? <Square size={16} aria-hidden />
            : <AudioLines size={16} aria-hidden />;
  const voiceOrbLabel =
    voiceErrored ? "Voice error"
    : vsnap.state === "speaking" ? "Stop"
    : vsnap.state === "listening" ? "Send now"
    : vsnap.state === "thinking" ? "Cancel - scrap that"
    : vsnap.state === "connecting" ? "Starting" : "Listening";

  // The single voice status surface, above the composer (replaces the ghost line).
  // One of: an error with Retry, the live partial transcript while listening, or
  // the plain state word. `aria-live="polite"` announces state changes.
  const voiceStatus: { kind: "error" | "partial" | "state"; text: string } =
    vsnap.error
      ? { kind: "error", text: vsnap.error }
      // A standing capture condition outlives any one turn's error, so it gets
      // its own branch: without this, clearing `error` made "no microphone"
      // silently become "Listening" again.
      : vsnap.micFail
        ? { kind: "error", text: MIC_FAIL_TEXT[vsnap.micFail] }
        : vsnap.state === "listening" && (vsnap.partial || vsnap.pendingUser)
          ? { kind: "partial", text: vsnap.partial || vsnap.pendingUser }
          : {
              kind: "state",
              text:
                vsnap.state === "connecting" ? "Connecting"
                : vsnap.state === "thinking" ? "Thinking"
                : vsnap.state === "speaking" ? "Speaking, Esc to stop"
                // "Listening" with the mic off is a lie, and it's the exact lie
                // that made a muted mic look like a broken one.
                : vsnap.state === "listening"
                  ? (vprefs.mic_enabled ? "Listening" : "Mic off — type to send")
                  : "Voice on",
            };

  const composer = (
    <div ref={composerRef} className={`chat-composer${streamingBuild ? " is-streaming" : ""}${inputFocused ? " is-focused" : ""}`}>
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
                // preventDefault on mousedown keeps the textarea focused; the
                // ACTION runs on click. It used to run on mousedown, which a
                // touch device only synthesizes after the fact and not
                // dependably — so on a phone, tapping a command did nothing at
                // all: no error, no message, no fold. click fires on both.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => runSlash(c)}
              >
                <span className="chat-slash-name">/{c.name}</span>
                <span className="chat-slash-desc">{c.desc}</span>
              </li>
            );
          })}
        </ul>
      )}
      {voiceOn && (
        <div className={`chat-voice-strip is-${voiceStatus.kind}`} role="status" aria-live="polite">
          {voiceStatus.kind === "error" ? (
            <>
              <span className="chat-voice-strip__msg">
                <AlertTriangle size={14} aria-hidden className="chat-voice-strip__icon" />
                {voiceStatus.text}
              </span>
              <Button size="sm" variant="ghost" onClick={() => vsession.retry()}>Retry</Button>
            </>
          ) : voiceStatus.kind === "partial" ? (
            <span className="chat-voice-strip__msg">
              <span className="chat-voice-strip__label">Heard:</span>
              <span className="chat-voice-strip__partial">{voiceStatus.text}</span>
            </span>
          ) : (
            <span className="chat-voice-strip__msg">
              <span className="chat-voice-strip__state">{voiceStatus.text}</span>
            </span>
          )}
        </div>
      )}
      <textarea
        ref={inputRef}
        className="chat-input"
        rows={1}
        value={input}
        placeholder={
          voiceOn
            ? (vprefs.mic_enabled ? "Talk, or type a message..." : "Type a message (mic muted)...")
            // Provider-neutral: this default also applies when the picker is
            // switched to an app assistant, so it can't name one scope.
            : (placeholder ?? (effectiveMode === "build" ? "Tell the agent what to build..." : "Message the assistant..."))
        }
        onChange={(e) => onComposerChange(e.target.value)}
        onKeyDown={onKey}
        onFocus={() => setInputFocused(true)}
        onBlur={() => setInputFocused(false)}
      />
      <div className="chat-actions">
        {!voiceOn && contextMeter}
        <span className="chat-actions__spacer" />
        {/* Model: a per-turn knob, so it lives with the other per-turn controls
            instead of in the header beside the things that change the whole
            surface. Menu rather than <select> — the stock double-arrow was the
            loudest element in a row of quiet icon buttons. */}
        {modelOptions.length > 1 ? (
          <Menu
            label="Model"
            align="end"
            side="top"
            trigger={
              <button className="chat-model" onMouseDown={(e) => e.preventDefault()} title="Model for this conversation">
                <span className="chat-model__val">{modelLabel(selectedModel)}</span>
                <ChevronDown size={14} aria-hidden className="chat-model__caret" />
              </button>
            }
          >
            {orderModels(modelOptions).map((id) => (
              <MenuItem key={id} checked={id === selectedModel} onSelect={() => setSelectedModel(id)}>
                {modelLabel(id)}
              </MenuItem>
            ))}
          </Menu>
        ) : modelOptions.length === 1 ? (
          <span className="chat-model is-static">
            <span className="chat-model__val">{modelLabel(modelOptions[0])}</span>
          </span>
        ) : null}
        {/* Voice mode: a mode of THIS chat (same thread + log), toggled in place.
            When on, the state orb, mic mute, reply mute, and settings sit here;
            when off, just the slash button. Build chats build by talking; data
            chats converse - either way it's the same conversation. */}
        {/* The voice cluster (orb, mic-mute, reply-mute, gear) shares a subtle
            background pill so it reads as one group, distinct from the exit
            toggle and Send that sit outside it. */}
        {voiceOn && (
          <div className={`chat-voice-cluster${voiceErrored ? " is-error" : ""}`}>
            <button
              ref={orbRef}
              className={`chat-voice-orb is-${vsnap.state}${voiceErrored ? " is-error" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={voiceOrbAction}
              aria-label={voiceOrbLabel}
              title={voiceOrbLabel}
            >
              {voiceOrbIcon}
            </button>
            <button
              className={`chat-slash-btn${vprefs.mic_enabled ? " is-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => vupdatePrefs({ mic_enabled: !vprefs.mic_enabled })}
              aria-pressed={vprefs.mic_enabled}
              aria-label={vprefs.mic_enabled ? "Mute microphone" : "Unmute microphone"}
              title={vprefs.mic_enabled ? "Mic on, tap to mute" : "Mic muted, tap to talk"}
            >
              {vprefs.mic_enabled ? <Mic size={16} aria-hidden /> : <MicOff size={16} aria-hidden />}
            </button>
            {/* Reply-mute ("Speak replies") lives in Voice settings, not here:
                a persistent TTS-off button next to the mic is the #1 thing that
                made this cluster confusing (two mute buttons doing different
                things). The main surface stays orb + mic-mute + settings. */}
            <button
              className="chat-slash-btn"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setVoiceSettings(true)}
              aria-label="Voice settings"
              title="Voice settings"
            >
              <Settings2 size={16} aria-hidden />
            </button>
          </div>
        )}
        <button
          className={`chat-slash-btn${voiceOn ? " is-active" : ""}`}
          onMouseDown={(e) => e.preventDefault()}
          onClick={toggleVoice}
          aria-pressed={voiceOn}
          aria-label={voiceOn ? "Exit voice mode" : "Voice mode"}
          title={voiceOn ? "Exit voice mode" : (build ? "Voice mode: build by talking" : "Voice mode: talk instead of type")}
        >
          {/* Distinct glyph on/off so the exit toggle never reads as a twin of the
              listening orb's AudioLines: AudioLines invites voice, PhoneOff leaves it. */}
          {voiceOn ? <PhoneOff size={16} aria-hidden /> : <AudioLines size={16} aria-hidden />}
        </button>
        {!voiceOn && (
          <button
            className={`chat-slash-btn${showSlash ? " is-active" : ""}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => { setSlashOpen((v) => !v); setSlashSel(0); }}
            aria-label="Slash commands"
            aria-expanded={showSlash}
            title="Slash commands"
          >
            <Slash size={16} aria-hidden />
          </button>
        )}
        {/* While a build turn streams, Send (queues the next message) sits to the
            left of Stop. Otherwise just Send. */}
        {/* onMouseDown preventDefault: don't let tapping an action button blur the
            textarea - on mobile that closes the keyboard, the layout jumps, and the
            tap lands where the button no longer is (the "send needs two taps" bug). */}
        <button className="chat-send" onMouseDown={(e) => e.preventDefault()} onClick={send} disabled={!input.trim()} aria-label="Send (Enter)" title={streamingBuild ? "Send (queues until the current turn finishes)" : "Send (Enter)"}>
          <CornerDownLeft size={16} aria-hidden />
        </button>
        {streamingBuild && (
          <button className="chat-stop" onMouseDown={(e) => e.preventDefault()} onClick={stop} aria-label="Stop receiving (turn continues in background)" title="Stop receiving (turn continues in background)">
            <Square size={16} aria-hidden />
          </button>
        )}
      </div>
    </div>
  );

  const panelInner = (
    <div className={`chat-panel chat-panel--${variant}`}>
      {toolbar}
      {spawnMsg && (
        <div className={`chat-spawn-note${spawnMsg.ok ? "" : " is-error"}`} role="status">
          <span className="chat-spawn-note__body">
            {spawnMsg.ok ? (
              <>
                {spawnMsg.ran ? (
                  <>Filed and running in <strong>{scopeLabel(spawnMsg.scope)}</strong>. The live status is in the card below.{" "}</>
                ) : spawnMsg.runError ? (
                  <>Filed in <strong>{scopeLabel(spawnMsg.scope)}</strong>, but could not run now: {spawnMsg.runError}.{" "}</>
                ) : (
                  <>Filed as a ticket in <strong>{scopeLabel(spawnMsg.scope)}</strong>.{" "}</>
                )}
                <a href={`/work?scope=${encodeURIComponent(spawnMsg.scope)}`}>View in Work</a>.{" "}
                <span className="ws-muted">{spawnMsg.text.slice(0, 140)}{spawnMsg.text.length > 140 ? "…" : ""}</span>
              </>
            ) : (
              spawnMsg.text
            )}
          </span>
          <button className="chat-tb__btn" onClick={() => setSpawnMsg(null)} title="Dismiss" aria-label="Dismiss">
            <X size={14} aria-hidden />
          </button>
        </div>
      )}
      {taskPanel}
      {log}
      {jumpToLatest}
      {egressBanner}
      {err && messages.length > 0 && !showEgressBanner && <div className="chat-err">{String(err).slice(0, 240)}</div>}
      {composer}
      {deleteDialog}
      {newChatDialog}
      <VoiceSettings
        open={voiceSettings}
        onClose={() => setVoiceSettings(false)}
        prefs={vprefs}
        updatePrefs={vupdatePrefs}
        caps={vsnap.caps}
        session={vsession}
      />
    </div>
  );

  if (variant === "full") {
    return <div className="ws-chat-full">{panelInner}</div>;
  }
  return panelInner;
}
