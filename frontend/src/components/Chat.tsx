import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  WifiOff, ArrowDown,
} from "lucide-react";
import { StateBlock, Button } from "../ui";
import TaskPanel from "./TaskPanel";
import InboxStrip from "./chat/InboxStrip";
import { NoticeBar, type FiledNotice } from "./chat/FiledNotice";
import ChatComposer from "./chat/ChatComposer";
import ChatToolbar from "./chat/ChatToolbar";
import ChatLog from "./chat/ChatLog";
import ChatDialogs from "./chat/ChatDialogs";
import { useCompaction } from "./chat/useCompaction";
import { useVoiceMode } from "./chat/useVoiceMode";
import {
  buildSlashCommands, matchSlash, matchMention, applyMention, type SlashCommand,
} from "./chat/slashCommands";
import { type ChatDrawer } from "./chat/drawers";
import {
  deriveMode, buildSlug, projectSlugFor, taskScopeFor, composerPlaceholder,
  isEgressFailure, type ChatMode, type ChatVariant,
} from "./chat/chatScope";
import { MODEL_LABELS } from "./chat/models";
import { useMessageQueue, type MessageQueue } from "./chat/useMessageQueue";
import { useLogScroll } from "./chat/useLogScroll";
import { useVoiceSettle } from "./chat/useVoiceSettle";
import { useTurnStream, useTurnLiveness } from "./chat/useTurnStream";
import {
  chatApi,
  type ChatMsg,
  type CardState,
  type Thread,
  type ThreadSummary,
  type Provider,
  shortHttpError,
} from "../lib/chat";
import { useDraft } from "../lib/draftSync";
import {
  NEW_CHAT, DEFAULT_SCOPE, resolveScope, readSelection, writeSelection, readProvider, writeProvider, writeQueue,
} from "../lib/chatSelection";
import VoiceSettings from "./VoiceSettings";
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

// Re-exported: these describe a chat, and callers have always imported them from
// here. Their definitions now live with the other contextKey derivations.
export type { ChatMode, ChatVariant };

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
  // Start BLANK, outranking every restore rule below. Set from `?new=1` (see
  // lib/chatSelection.newChatHref) because a "New chat" button elsewhere in the
  // app knows the context key but cannot compute the scope the selection is
  // keyed by — so it states the intent and lets this component record it.
  startNew?: boolean;
};

export default function Chat({ contextKey, mode, variant = "panel", placeholder, onAction, scoped, hideExpand, initialThreadId, startNew }: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();

  const [providers, setProviders] = useState<Provider[]>([]);
  const showProviderPicker = variant === "full" || effectiveMode === "data";
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);

  const [selectedModel, setSelectedModel] = useState<string>("");

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  // The live turn IS the CLI's own /compact. It runs through the ordinary turn
  // machinery, so without this the indicator said "Working" — the one label that
  // tells you nothing about the several seconds you are waiting through.
  const [turnIsCompact, setTurnIsCompact] = useState(false);
  // Which in-chat cards this thread has already acted on (server-backed), so a
  // proposal that was filed and run doesn't come back offering to run again.
  const [cardStates, setCardStates] = useState<Record<string, CardState>>({});
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
  const [input, setInput, clearInput] = useDraft(contextKey);
  // Composer focus: drives mobile keyboard chrome (drop the bottom padding so
  // there's no dead row between the buttons and the keyboard) + scroll pinning.
  const [inputFocused, setInputFocused] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);

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

  // The turn stream and the type-ahead queue each need the other: the queue
  // must know whether a turn is running, and a finished turn must dispatch what
  // is queued behind it. The stream is declared first (the queue reads its
  // `streaming` directly) and reaches the queue through a ref filled in below.
  // Set only when this chat files a ticket — see chat/FiledNotice for why that
  // is the only thing allowed above the log.
  const [notice, setNotice] = useState<FiledNotice | null>(null);

  const queueRef = useRef<MessageQueue | null>(null);

  // The live turn: its SSE channel, resilience, and what "still running" means.
  // See useTurnStream for the rules a dropped connection has to obey.
  const {
    streaming, logBusyRef, reconnecting, elapsed,
    pendingSegments, setPendingSegments,
    attach: attachStream, anchorStart, stop, closeStream, hasOpenStream,
    markStarting, markFailed, segmentCount,
  } = useTurnStream({
    onMessages: (msgs) => setMessages(msgs),
    onTurnEnd: (wasFirst) => {
      void refreshThreads();
      scheduleAutoNameRefetch(wasFirst);
      setTaskRefetchKey((k) => k + 1);
    },
    onError: setErr,
    onNotes: () => onAction?.(),
    activeIdRef,
    dispatchQueue: () => queueRef.current?.dispatch(),
    appendSegmentsAsMessage: (segs) => setMessages((m) => [...m, {
      role: "assistant", content: "", ts: new Date().toISOString(), meta: { segments: segs },
    }]),
  });

  // Does a turn still own the TAIL of the log? The render-time twin of
  // useTurnStream's logBusyRef, and the same condition ChatLog renders the live
  // band on — so "there is a turn at the end of the transcript" has exactly one
  // answer, whoever asks. `streaming` alone is not that answer: it goes false
  // while the reply is still being read back from the server.
  const logBusy = streaming || pendingSegments.length > 0;

  // Messages typed while a build turn is streaming are queued (a session can't
  // run two turns at once) and sent automatically when the turn finishes. They
  // render as pending user bubbles below the live response. The durability and
  // self-draining rules live in the hook — see useMessageQueue.
  const sendBuildRef = useRef<(t: string) => void>(() => {});
  // A /compact is in flight. Declared HERE rather than inside useCompaction
  // because the two things that must hold for it — this queue and submitText —
  // are both declared before it. While it is set, a typed message joins the
  // queue instead of becoming a turn, so it cannot overtake the command that
  // was asked for first. See useCompaction's header.
  const [compacting, setCompacting] = useState(false);
  const queue = useMessageQueue({
    scope: effectiveMode === "build" ? contextKey : activeProvider,
    streaming, loadingThreads, held: compacting, logBusyRef,
    send: (t) => sendBuildRef.current(t),
    appendUserMessages: (texts) => {
      const ts = new Date().toISOString();
      setMessages((m) => [...m, ...texts.map((content) => ({ role: "user" as const, content, ts }))]);
    },
  });
  // Close the loop the stream reads through. Without this assignment
  // `dispatchQueue` above resolves to null on every finished turn, and the
  // queue only drains via its own self-draining effect a beat later — the
  // primary hand-off is simply dead.
  queueRef.current = queue;

  // /compact reaches BOTH homes of a chat's context — see useCompaction.
  const {
    compactedThrough, setCompactedThrough, ctxNonce, compact, queuedStatus,
  } = useCompaction({
    activeIdRef, logBusyRef, queue,
    setCompacting, setMessages, setErr,
    sendTurn: (t) => void sendBuildRef.current(t),
  });

  const [taskRefetchKey, setTaskRefetchKey] = useState(0);
  const [openTaskCount, setOpenTaskCount] = useState(0);
  // Filing this chat as tickets. The dialog owns the drafting, editing and
  // filing; this only holds whether it is open and where its result note goes.
  const [ticketsOpen, setTicketsOpen] = useState(false);

  // P3: agents this chat may @-mention. Server-resolved (the rule is opt-in +
  // scope reach, which is policy, not display), refreshed when the thread moves.
  const [mentionable, setMentionable] = useState<
    { slug: string; name: string; role: string | null; purpose: string; local: boolean }[]
  >([]);
  useEffect(() => {
    if (!activeId) { setMentionable([]); return; }
    let cancelled = false;
    fetch(`/api/chat/threads/${encodeURIComponent(activeId)}/mentionable`)
      .then((r) => (r.ok ? r.json() : { agents: [] }))
      .then((j) => { if (!cancelled) setMentionable(j.agents || []); })
      .catch(() => { if (!cancelled) setMentionable([]); });
    return () => { cancelled = true; };
  }, [activeId]);

  // One auxiliary drawer at a time — see chat/drawers for why.
  const [drawer, setDrawer] = useState<ChatDrawer>(null);
  const inboxOpen = drawer === "inbox";
  const tasksOpen = drawer === "tasks";
  const [inboxCount, setInboxCount] = useState(0);
  const [slashSel, setSlashSel] = useState(0);
  const [mentionSel, setMentionSel] = useState(0);
  // The slash BUTTON opens the command list as a menu. It is deliberately
  // separate from "the operator is typing a /command": the button used to work
  // by writing "/" into the composer, which meant tapping it destroyed whatever
  // had been typed. A picker must never edit the thing you were writing.
  const [slashOpen, setSlashOpen] = useState(false);
  const slug = buildSlug(contextKey);
  const taskScope = taskScopeFor(contextKey);

  // Stream resilience: count consecutive reconnect attempts (reset on any event
  // received, so a healthy long turn never trips the cap), and self-reference
  // attachStream so a drop handler can re-attach without a render cycle.


  // The two URL intents — `?new=1` (start blank) and `?thread=<id>` (open this
  // conversation) — are ONE-SHOT: each is something the operator just clicked,
  // so it is consumed on use and cannot be re-armed by a re-render. They are
  // held in refs for that reason. The persisted selection is not an intent; it
  // is the fallback for a mount the URL says nothing about.
  //
  // Both are declared ABOVE the scope resolver, because the resolver has to READ
  // them (the intent decides the scope) without CONSUMING them (the thread
  // loader does that, once, after the scope is settled).
  const startNewRef = useRef(!!startNew);
  const linkedThreadRef = useRef<string | null>(initialThreadId ?? null);
  // Re-arm when the URL names a different thread, or asks for a new chat, while
  // this Chat stays mounted (list -> chat -> back -> another chat keeps the same
  // route). Declared above the loader so they land first in the same commit.
  useEffect(() => {
    linkedThreadRef.current = initialThreadId ?? null;
  }, [initialThreadId]);
  useEffect(() => {
    if (startNew) startNewRef.current = true;
  }, [startNew]);

  // Which scope this mount belongs in — resolved ONCE, before any thread loads.
  //
  // `scope` is the thread list you can see, and it is the SAME axis as a
  // thread's server-side context_key: list_threads filters on an exact match. So
  // a thread already knows where it lives, and opening one can never be a
  // question about which scope the picker happens to be left on.
  //
  // It used to be exactly that question, and that is the bug behind every
  // variant of "my chats open blank / open the wrong one":
  //   * activeProvider started at contextKey and then flipped asynchronously
  //     once listProviders resolved, so the thread loader ran TWICE — the first
  //     pass burned the one-shot URL intent under the wrong scope, and the
  //     second pass ran with no intent left and fell back to whatever this scope
  //     was last on. `?new=1` reopened an old chat; `?thread=<id>` opened a
  //     different one, or nothing, because the linked thread wasn't in the scope
  //     the picker had drifted to. Whether it misbehaved depended on which of
  //     two async calls landed first, which is why it looked intermittent.
  //   * the remembered provider is keyed by contextKey, and nearly every chat
  //     lives at /chat/general — so it was in practice ONE global "last scope I
  //     picked" that every chat inherited. Switch the picker to the budget
  //     assistant once and every chat opened afterwards came up in budget scope.
  //
  // The order below is intent-first, exactly like the thread loader's:
  //   ?thread=<id> → the thread's own context_key. Server state wins; a chat
  //                  always opens in the scope it actually belongs to.
  //   ?new=1       → the surface the button was clicked on. A blank chat starts
  //                  in the workshop-wide scope ("general") unless it was
  //                  launched from a scoped surface (a project, an app dock),
  //                  in which case it inherits that. It never inherits the
  //                  picker's memory.
  //   neither      → the provider this surface was last left on: a bare return
  //                  to a chat you were already in.
  // The resolved scope is written back, so the picker shows where you are and
  // the memory can't disagree with the thread on screen.
  const [providersReady, setProvidersReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let list: Provider[] = [];
      try {
        list = await chatApi.listProviders(scoped ? contextKey : undefined);
        if (cancelled) return;
        setProviders(list);
      } catch (e) {
        // NOT non-essential, which is what this used to claim. This list is the
        // live source for the model picker; when it fails the picker silently
        // falls back to the frontend's hardcoded MODEL_LABELS, which can offer
        // models the backend does not serve — the same frontend/backend
        // divergence that made a newly added model invisible. Say it failed
        // rather than presenting a stale list as the real one.
        if (!cancelled) setErr(`Couldn't load the model/assistant list: ${e}. The picker may be showing stale options.`);
      }
      if (cancelled) return;
      if (effectiveMode === "data" && list.length) {
        const linked = linkedThreadRef.current;
        let linkedThreadHome: string | null = null;
        if (linked) {
          // Read-only peek: the loader still fetches and adopts this thread.
          const t = await chatApi.getThread(linked).catch(() => null);
          if (cancelled) return;
          if (t) linkedThreadHome = String(t.context_key || DEFAULT_SCOPE);
        }
        const next = resolveScope({
          contextKey,
          knownScopes: list.map((p) => p.key),
          linkedThreadHome,
          startNew: startNewRef.current,
          remembered: readProvider(contextKey),
        });
        setActiveProvider(next);
        writeProvider(contextKey, next);
      }
      if (!cancelled) setProvidersReady(true);
    })();
    return () => { cancelled = true; };
  }, [contextKey, effectiveMode, scoped, initialThreadId, startNew]);

  const chooseProvider = useCallback((key: string) => {
    setActiveProvider(key);
    writeProvider(contextKey, key);
  }, [contextKey]);

  // How this chat handles work: Direct (stays here, native subagents) or
  // Orchestrated (leaves as tickets). Server state, mirrored locally so the
  // control responds immediately; the server re-states the mode to the model on
  // every turn, so switching mid-conversation takes effect on the next one.
  const [chatMode, setChatMode] = useState<string>("orchestrated");
  const chooseChatMode = useCallback((next: "direct" | "orchestrated") => {
    const id = activeIdRef.current;
    if (!id) return;
    const prev = chatMode;
    setChatMode(next);
    void chatApi.setThreadMode(id, next).catch((e) => {
      // Put the control back rather than showing a mode the server does not
      // have: a chat that looks Direct while the server files tickets is worse
      // than a visible failure.
      setChatMode(prev);
      setErr(`Couldn't switch chat mode: ${e}`);
    });
  }, [chatMode]);

  const scope = effectiveMode === "build" ? contextKey : activeProvider;
  const providerKey = effectiveMode === "build" ? contextKey : activeProvider;
  // Take `?new=1` back out of the URL once acted on, so a refresh is governed by
  // the persisted selection instead of blanking a thread that now exists.
  const stripNewParam = useCallback(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (!url.searchParams.has("new")) return;
    url.searchParams.delete("new");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }, []);
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
  // A mode of THIS chat, not a separate screen — see useVoiceMode for the
  // lifecycle rules (gesture-scoped audio unlock, driving off the flag rather
  // than the toggle, and what "fatal" is allowed to mean).
  const {
    voiceOn, toggleVoice, settingsOpen: voiceSettings, setSettingsOpen: setVoiceSettings,
    orbRef, vsession, vsnap, vprefs, vupdatePrefs,
  } = useVoiceMode({
    contextKey,
    model: selectedModel,
    activeId,
    onAdoptThread: (id) => {
      if (!activeIdRef.current) {
        setActiveId(id);
        // Persist the selection too. Adopting without it left a voice-started
        // chat unreachable on the next mount: the stored selection still said
        // NEW_CHAT, so the loader came back blank and the thread the operator
        // had just been talking to was only findable in the picker.
        writeSelection(scope, id);
        queue.carryToThread(id);
      }
      void refreshThreads();
    },
    onFatal: setErr,
  });

  // A finished spoken exchange stays on screen until its persisted messages
  // land — see useVoiceSettle for why this needs a layout effect and a
  // ref-driven poll.
  const { settling } = useVoiceSettle({
    voiceOn, activeId, vsnap, messagesRef,
    setMessages: (msgs) => setMessages(msgs),
    refreshThreads: () => { void refreshThreads(); },
  });

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

  const hydrate = useCallback(
    (t: Thread) => {
      // Point the queue at the thread being loaded BEFORE reading it, so the
      // read-then-write round trip can't land on the thread we're leaving.
      queue.bindThread(t.id);
      // The fold marker is server state, not a one-shot response to clicking
      // /compact: without reading it back here the divider vanished on every
      // reload and a compacted thread looked exactly like an uncompacted one.
      setCompactedThrough(Number(t.compacted_through) || 0);
      setCardStates(t.card_states ?? {});
      // Server state: the mode belongs to the thread, not to this tab.
      setChatMode(t.mode || "orchestrated");
      const msgs = t.messages || [];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant" && last.meta?.streaming && last.meta.turn_id) {
        // Anchor the elapsed clock to the turn's REAL start (the streaming
        // message's server timestamp), so a reload mid-turn shows the true
        // elapsed time instead of restarting from zero.
        anchorStart(last.ts ? Date.parse(last.ts) : NaN);
        setMessages(msgs.slice(0, -1));
        attachStream(last.meta.turn_id, t.id, false);
      } else {
        setMessages(msgs);
        setPendingSegments([]);
      }
    },
    [attachStream, anchorStart, setPendingSegments, queue],
  );

  useEffect(() => {
    // Gate: never load a thread under a scope that is still being resolved.
    // Without this the loader runs once on the initial guess and again on the
    // real scope, and the one-shot URL intents are consumed by the first pass.
    if (!providersReady) return;
    let cancelled = false;
    (async () => {
      setErr(null);
      setLoadingThreads(true);
      try {
        const list = await chatApi.listThreads(scope);
        if (cancelled) return;
        setThreads(list);
        // Choosing the active thread, in ONE order, from two kinds of input:
        //
        //   URL intent (a click that just happened) — outranks everything:
        //     `?new=1`      → blank, and record it under the scope key, which
        //                     only this component can compute
        //     `?thread=<id>`→ that conversation
        //   Persisted state (what to do when the URL says nothing):
        //     the thread we're already on → re-hydrate, never snap to newest
        //     NEW_CHAT recorded           → stay blank across reloads
        //     the last thread in scope    → restore it
        //     otherwise                   → the most recent thread
        //
        // The link outranking NEW_CHAT is the whole point: a recorded "I want a
        // blank chat" is older than the click asking for a specific one. Ranked
        // the other way, every chat in the list opened blank once New chat had
        // been pressed; ranked with no link intent at all, New chat reopened the
        // thread you had just left. Both are the same bug, which is why they are
        // now one rule instead of two special cases.
        const has = (id: string | null | undefined): boolean => !!id && list.some((x) => x.id === id);
        if (startNewRef.current) {
          startNewRef.current = false;
          // The two URL intents are mutually exclusive; taking one disarms the
          // other so a stale `?thread` can't undo an explicit New chat.
          linkedThreadRef.current = null;
          writeSelection(scope, NEW_CHAT);
          stripNewParam();
        }
        const linked = linkedThreadRef.current;
        linkedThreadRef.current = null;
        const stored = readSelection(scope);
        // Where this scope lands with no link: the persisted ranking.
        const fallbackTarget = (): string | null => {
          const current = activeIdRef.current;
          if (has(current)) return current;
          if (stored === NEW_CHAT) return null;
          if (has(stored)) return stored;
          return list.length ? list[0].id : null;
        };
        // A linked thread is fetched by id, not looked up in `list`: the chat
        // list spans every context, so the thread the operator clicked need not
        // live in THIS scope. If it's gone, fall back as if no link were given
        // rather than stranding the chat on an error.
        let t: Thread | null = null;
        if (linked) t = await chatApi.getThread(linked).catch(() => null);
        if (cancelled) return;
        if (!t) {
          const target = fallbackTarget();
          t = target ? await chatApi.getThread(target) : null;
        }
        if (t) {
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
          queue.bindBlank();
        }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      } finally {
        if (!cancelled) setLoadingThreads(false);
      }
    })();
    return () => { cancelled = true; closeStream(); };
    // hydrate is stable enough; scope and the linked thread drive the reload —
    // the URL naming a different conversation has to re-run this even when the
    // route (and therefore the scope) is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, initialThreadId, providersReady]);

  // Liveness is SERVER state — a turn running in another tab or on another
  // device must not render as an idle chat. See useTurnLiveness.
  useTurnLiveness({
    activeId, streaming, hasOpenStream,
    onReattach: (turnId, threadId, startedMs, msgs) => {
      anchorStart(startedMs);
      setErr(null);
      setMessages(msgs);
      attachStream(turnId, threadId, false);
    },
  });

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
      if (cancelled || streaming || hasOpenStream() || document.hidden) return;
      if (messagesRef.current.some((m) => m.meta?.unsent)) return;
      try {
        const t = await chatApi.getThread(activeId);
        if (cancelled || streaming || hasOpenStream()) return;
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

  // Following the conversation, composer sizing, and the jump-to-latest
  // affordance are one behaviour — see useLogScroll for the rules and the bugs
  // each of them exists to prevent.
  const {
    scrollRef, composerRef, inputRef, showJumpToLatest, onLogScroll, followLatest,
  } = useLogScroll({
    input,
    inputFocused,
    followDeps: [messages, pendingSegments, interjections, queue.items, streaming,
                 vsnap.state, vsnap.sentences, vsnap.lastUser],
    hasContent: streaming || pendingSegments.length > 0 || messages.length > 0,
  });

  // (There is no in-chat "open another thread" path any more. Switching
  // conversations goes through the chat list, which navigates here with
  // ?thread=<id>; the mount effect above adopts it and persists the selection.)

  const newChat = useCallback(() => {
    closeStream();
    setActiveId(null);
    setMessages([]);
    setPendingSegments([]);
    // Move the queue key to the blank-chat slot FIRST. Otherwise clearing the
    // composer queue for the new chat would wipe the queue still owed to the
    // thread being left behind.
    queue.resetToBlank();
    setErr(null);
    setSelectedModel("");
    setCompactedThrough(0);
    setCardStates({});
    // Persist the intent so a reload / navigation lands on a blank new chat
    // rather than silently re-adopting the most-recent thread.
    writeSelection(scope, NEW_CHAT);
  }, [queue, scope, closeStream, setPendingSegments]);

  const removeThread = useCallback(async (id: string) => {
    await chatApi.deleteThread(id);
    // Deleting the conversation you are looking at has to take you somewhere.
    // The focused route is nothing BUT that conversation, so staying put left a
    // chat page addressing a thread the server no longer has — the composer
    // aimed at a dead id and the transcript stuck on its last render. Leave for
    // the chat list, which is the surface that still has something to show.
    // A docked/panel chat has no route to leave, so it falls back to blank.
    if (id === activeId) {
      writeSelection(scope, NEW_CHAT);
      writeQueue(id, []);
      if (variant === "full") {
        navigate("/chats");
        return;
      }
      newChat();
    }
    refreshThreads();
  }, [activeId, newChat, refreshThreads, scope, variant, navigate]);

  // The universal turn sender. EVERY chat (general, app, project, build) streams
  // through POST /threads/{id}/turns + the SSE channel, so every turn shows its
  // work (tool calls, interim text) live instead of a blocking spinner. The
  // backend picks the tool surface + session from the provider class; the client
  // is transport-uniform.
  const sendBuild = useCallback(async (text: string) => {
    markStarting();
    setTurnIsCompact(text.trim() === "/compact");
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
        queue.carryToThread(t.id);
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
      markFailed();
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
  }, [activeId, scope, selectedModel, messages.length, attachStream, queue]);

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

  // Submit a concrete string as a turn (used by send() and by slash passthrough).
  const submitText = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text) return;
    setErr(null);
    // A note describes the LAST thing the operator did. Sending makes it stale,
    // so it goes with the error it sits beside. Without this the two transient
    // surfaces had different lifetimes — errors cleared on the next send, notes
    // never did — and a "Filed as a ticket" banner from twenty minutes ago was
    // still sitting above a conversation that had long moved on.
    setNotice(null);
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
    // A /compact asked for a moment ago is still folding the transcript
    // server-side and has not yet been able to send its live half. Sending this
    // now would put a message the operator typed SECOND into the session first,
    // and the compaction would then land behind it — which is precisely the
    // "my message got inserted above compact" report. Queue it; the fold
    // releases the hold and the queue drains in the order things were typed.
    if (compacting) { queue.push(text); return; }
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
    // WHAT "DELIVERED" MEANS, precisely, because the UI used to overstate it:
    // the bytes reached the running process. Measured against the CLI (see
    // docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md §2), the
    // agent then addresses the message at the END of its current plan — 24.5s
    // and four skipped step boundaries in the probe — not "within a step or
    // two", which is what the comment below used to claim and what the composer
    // implied. The message is genuinely handed over; it is not read yet.
    //
    // ...unless it is a slash command. Injection hands the text to the MODEL;
    // slash commands are handled by the CLI's own command layer, which only runs
    // between turns. A command that arrives mid-turn is swallowed there — not
    // deferred, not passed through as text, just gone. Measured against the CLI
    // directly (2.1.220): "/compact" injected mid-turn produced no user message,
    // no command, no fold, and the turn ended normally; the same channel, same
    // message shape, sent while idle, emitted a fresh session init and a real
    // reply. Plain text injected mid-turn arrives fine (that's the control), so
    // this is specific to the leading slash. Queue commands to run as their own
    // turn, which is the only timing the CLI acts on them.
    const isSlashCommand = /^\/[a-zA-Z][\w:-]*(\s|$)/.test(text);
    const threadId = activeIdRef.current;
    if (logBusy && isSlashCommand) { queue.push(text); return; }
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
            setInterjections((x) => [...x, { at: segmentCount(), text }]);
            followLatest();
          } else {
            queue.push(text);
          }
        })
        .catch(() => { queue.push(text); });
      return;
    }
    // Not streaming any more, but the finished turn is still occupying the tail
    // of the log while its reply lands in `messages`. Appending here is exactly
    // what put a new message ABOVE a turn that had already completed — the queue
    // renders below the live band, which is the truthful place for something
    // that has not been sent yet. It drains a beat later, in order.
    if (logBusy) { queue.push(text); return; }
    const userMsg: ChatMsg = { role: "user", content: text, ts: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    sendBuild(text);
  }, [streaming, logBusy, compacting, sendBuild, queue, compact, followLatest]);

  // Composer text change. In voice mode, keep the engine's typing-hold aware of
  // whether the composer currently holds text (so it defers taking its turn).
  const onComposerChange = useCallback((v: string) => {
    setInput(v);
    if (voiceOn) vsession.setTyping(v.trim().length > 0);
  }, [setInput, voiceOn, vsession]);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    // clearInput, not setInput(""): a send-clear must be written through to
    // every synced surface immediately and then defended, or a stale copy from
    // another tab/device can restore the text you just sent.
    clearInput();
    // In voice mode a typed message runs through the voice engine too (so the
    // reply is spoken and uses the warm worker); slash-commands still go the
    // normal chat route.
    if (voiceOn && !text.startsWith("/")) {
      vsession.submitText(text);
      vsession.setTyping(false);
    } else {
      submitText(text);
    }
  }, [input, submitText, voiceOn, vsession, clearInput]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // The mention picker owns the same keys as the slash menu while it is open,
    // and is checked first: typing "@" is a narrower, more recent intent than a
    // "/" that may be sitting at the start of the line from earlier.
    if (showMentions) {
      const sel = Math.min(mentionSel, mentionMatches.length - 1);
      if (e.key === "ArrowDown") { e.preventDefault(); setMentionSel((i) => Math.min(i + 1, mentionMatches.length - 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setMentionSel((i) => Math.max(i - 1, 0)); return; }
      if (e.key === "Escape") { e.preventDefault(); setMentionSel(0); setInput(input.replace(/@[a-z0-9-]*$/, "")); return; }
      if (e.key === "Tab" || e.key === "Enter") { e.preventDefault(); pickMention(mentionMatches[sel].slug); return; }
    }
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
  const build = effectiveMode === "build";
  const slashCommands = buildSlashCommands({
    isBuild: build,
    hasThread: !!activeId,
    hasTaskScope: !!taskScope,
    canExpand: variant !== "full" && !hideExpand,
    compacting,
    actions: {
      newChat: () => newChat(),
      deleteChat: () => { if (activeId) setConfirmDeleteId(activeId); },
      toggleTasks: () => setDrawer((d) => (d === "tasks" ? null : "tasks")),
      toggleInbox: () => setDrawer((d) => (d === "inbox" ? null : "inbox")),
      popOut: () => popOut(),
      showHelp: () => setSlashOpen(true),
      openDeploys: () => {
        const p = projectSlugFor(contextKey);
        navigate(p ? `/projects/${encodeURIComponent(p)}#deploys` : "/deploys");
      },
      compact: () => void compact(),
    },
  });
  const { typed: slashTyped, matches: slashMatches, show: showSlash } =
    matchSlash(input, slashCommands, slashOpen);
  const { matches: mentionMatches, show: showMentions } = matchMention(input, mentionable);

  const pickMention = (slug: string) => {
    setInput(applyMention(input, slug));
    setMentionSel(0);
    inputRef.current?.focus();
  };
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
  const toolbar = (
    <ChatToolbar
      variant={variant}
      mode={effectiveMode}
      slug={slug}
      hideExpand={hideExpand}
      activeId={activeId}
      activeThreadTitle={activeThreadTitle}
      showProviderPicker={showProviderPicker}
      providers={providers}
      activeProvider={activeProvider}
      onChooseProvider={chooseProvider}
      chatMode={chatMode}
      onChooseChatMode={chooseChatMode}
      drawer={drawer}
      onSetDrawer={setDrawer}
      inboxCount={inboxCount}
      taskScope={taskScope}
      openTaskCount={openTaskCount}
      onBack={back}
      onOpenWorkbench={() => navigate(`/agents/${slug}/chat`)}
      onFileTicket={() => setTicketsOpen(true)}
      onNewChat={() => setConfirmNew(true)}
      onDelete={setConfirmDeleteId}
      onPopOut={popOut}
      onNavigate={navigate}
    />
  );

  const dialogs = (
    <ChatDialogs
      scope={scope}
      activeId={activeId}
      activeThreadTitle={activeThreadTitle}
      ticketsOpen={ticketsOpen}
      onCloseTickets={() => setTicketsOpen(false)}
      onFiled={setNotice}
      confirmNew={confirmNew}
      onCancelNew={() => setConfirmNew(false)}
      onConfirmNew={() => { setConfirmNew(false); newChat(); }}
      confirmDeleteId={confirmDeleteId}
      confirmDeleteTitle={confirmDeleteId ? threads.find((t) => t.id === confirmDeleteId)?.title : undefined}
      onCancelDelete={() => setConfirmDeleteId(null)}
      onConfirmDelete={async (id) => { setConfirmDeleteId(null); await removeThread(id); }}
    />
  );

  const overflowed = messages.length >= 500;

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

  const jumpToLatest = showJumpToLatest ? (
    <div className="chat-jump">
      <Button size="sm" variant="ghost" onClick={followLatest}>
        <ArrowDown size={14} aria-hidden /> Jump to latest
      </Button>
    </div>
  ) : null;

  const log = (
    <ChatLog
      scrollRef={scrollRef}
      onScroll={onLogScroll}
      loading={loadingThreads}
      error={err}
      messages={messages}
      isBuild={effectiveMode === "build"}
      inFlight={inFlight}
      overflowed={overflowed}
      compactedThrough={compactedThrough}
      selectedModel={selectedModel}
      cardCtx={cardCtx}
      onRetryUnsent={retryUnsent}
      streaming={streaming}
      logBusy={logBusy}
      pendingSegments={pendingSegments}
      interjections={interjections}
      workingLabel={reconnecting ? "Reconnecting" : turnIsCompact ? "Compacting" : "Working"}
      elapsed={elapsed}
      queued={queue.items}
      queuedStatus={queuedStatus}
      voiceOn={voiceOn}
      settling={settling}
      vsnap={vsnap}
    />
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

  const composer = (
    <ChatComposer
      composerRef={composerRef}
      inputRef={inputRef}
      input={input}
      onChange={onComposerChange}
      onKeyDown={onKey}
      placeholder={
        voiceOn
          ? (vprefs.mic_enabled ? "Talk, or type a message..." : "Type a message (mic muted)...")
          // Names the scope you are typing into (see composerPlaceholder). A
          // host that passes its own placeholder still wins.
          : (placeholder ?? composerPlaceholder(scope))
      }
      focused={inputFocused}
      onFocusChange={setInputFocused}
      slashOptions={slashMatches.map((c) => ({ key: c.name, label: `/${c.name}`, desc: c.desc }))}
      slashSelected={slashSel}
      showSlash={showSlash}
      onSlashHover={setSlashSel}
      onSlashPick={(name) => {
        const c = slashMatches.find((x) => x.name === name);
        if (c) runSlash(c);
      }}
      onToggleSlash={() => { setSlashOpen((v) => !v); setSlashSel(0); }}
      mentionOptions={mentionMatches.map((a) => ({
        key: a.slug,
        label: `@${a.slug}`,
        desc: `${a.role ? `${a.role} · ` : ""}${a.purpose || a.name}`,
      }))}
      mentionSelected={mentionSel}
      showMentions={showMentions}
      onMentionHover={setMentionSel}
      onMentionPick={pickMention}
      streaming={streamingBuild}
      onSend={send}
      onStop={stop}
      canSend={!!input.trim()}
      modelOptions={modelOptions}
      selectedModel={selectedModel}
      onSelectModel={setSelectedModel}
      activeId={activeId}
      messages={messages}
      ctxNonce={ctxNonce}
      voiceOn={voiceOn}
      onToggleVoice={toggleVoice}
      isBuild={build}
      vsnap={vsnap}
      vprefs={vprefs}
      vsession={vsession}
      orbRef={orbRef}
      onUpdatePrefs={vupdatePrefs}
      onOpenVoiceSettings={() => setVoiceSettings(true)}
    />
  );

  const panelInner = (
    <div className={`chat-panel chat-panel--${variant}`}>
      {toolbar}
      {notice && <NoticeBar notice={notice} onDismiss={() => setNotice(null)} />}
      <InboxStrip
        chatId={activeId}
        open={inboxOpen}
        onCountChange={setInboxCount}
        onClose={() => setDrawer(null)}
        onOpenGlobal={() => navigate("/work?tab=inbox")}
      />
      {taskPanel}
      {log}
      {jumpToLatest}
      {egressBanner}
      {/* Not gated on message count. It used to require `messages.length > 0`,
          which silently swallowed every error on a fresh chat — including the
          model-list load failure, which happens exactly there, and compact's own
          "this chat has no messages yet", a message that by construction could
          never be seen. The egress banner is the one case that already says it
          better. */}
      {err && !showEgressBanner && <div className="chat-err">{String(err).slice(0, 240)}</div>}
      {composer}
      {dialogs}
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
