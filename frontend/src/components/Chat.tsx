import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { WifiOff, ArrowDown } from "lucide-react";
import { StateBlock } from "../ui";
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
import { useMessageQueue } from "./chat/useMessageQueue";
import { useLogScroll } from "./chat/useLogScroll";
import { useVoiceSettle } from "./chat/useVoiceSettle";
import { useTurnStream } from "./chat/useTurnStream";
import { useThreadLog } from "./chat/useThreadLog";
import { useChatScope } from "./chat/useChatScope";
import { useChatThread } from "./chat/useChatThread";
import { useSendPipeline, wasFirstExchange } from "./chat/useSendPipeline";
import { canDrainQueue } from "./chat/sendPlan";
import { clampError } from "./chat/chatLogState";
import { staleNoticeText } from "./chat/staleNotice";
import { elapsedSeconds, turnLabel } from "./chat/turnStatus";
import { committedMessages } from "./chat/transcriptBlocks";
import { chatApi, type CardState } from "../lib/chat";
import { useDraft } from "../lib/draftSync";
import VoiceSettings from "./VoiceSettings";
import { type UiCardCtx } from "./chat/uiCardActions";

// The unified chat. COMPOSITION over one store and a handful of hooks, each of
// which owns one seam:
//
//   useThreadLog      the thread's event log, read from ONE endpoint by cursor,
//                     plus the transcript derived from it
//   useTurnStream     the live run's SSE socket, folding into that same store
//   useChatScope      which provider/scope this mount is in, and which model
//   useChatThread     which conversation is open and its settings
//   useSendPipeline   what happens to a message the operator hit enter on
//   useMessageQueue   type-ahead, and its five durability invariants
//   useCompaction     /compact and /clear, and the order they must keep
//   useLogScroll      following the conversation, and jump-to-latest
//   useVoiceMode      voice as a mode of this chat, not a separate screen
//
// variant controls chrome:
//   "dock"  - floating bottom-sheet contents
//   "panel" - inline, fills its host region (build IDE chat column)
//   "full"  - full-page, centred, fills the viewport (FocusedChat route)
//
// All variants share ONE compact toolbar (scope picker + new/delete + tasks +
// model + expand) so nothing stacks into wasted vertical rows, and the composer
// is an auto-growing textarea that stays editable while a turn is streaming.

// Re-exported: these describe a chat, and callers have always imported them from
// here. Their definitions now live with the other contextKey derivations.
export type { ChatMode, ChatVariant };

// How often to re-ask "the server says a run is live and I am not watching it".
// Short enough that a dropped stream is picked back up before the operator
// notices the work stopped moving; it only runs while a run is in flight.
const LIVENESS_MS = 5_000;

export type ChatProps = {
  contextKey: string;
  mode?: ChatMode;
  variant?: ChatVariant;
  placeholder?: string;
  onAction?: () => void;
  scoped?: boolean;
  // Hide the toolbar's "open full screen" pop-out. Set when the host already
  // owns a single expand control for the chat (e.g. AgentBuild's region focus).
  hideExpand?: boolean;
  // Open a SPECIFIC thread on mount (e.g. resuming a draft project chat from a
  // link) instead of the most-recent one for this scope.
  initialThreadId?: string;
  // Start BLANK, outranking every restore rule. Set from `?new=1` (see
  // lib/chatSelection.newChatHref) because a "New chat" button elsewhere in the
  // app knows the context key but cannot compute the scope the selection is
  // keyed by — so it states the intent and lets this component record it.
  startNew?: boolean;
};

export default function Chat({
  contextKey, mode, variant = "panel", placeholder, onAction, scoped, hideExpand,
  initialThreadId, startNew,
}: ChatProps) {
  const effectiveMode: ChatMode = mode ?? deriveMode(contextKey);
  const navigate = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  // Set only when this chat files a ticket — see chat/FiledNotice for why that
  // is the only thing allowed above the log.
  const [notice, setNotice] = useState<FiledNotice | null>(null);

  // The two URL intents — `?new=1` (start blank) and `?thread=<id>` (open this
  // conversation) — are ONE-SHOT: each is something the operator just clicked,
  // so it is consumed on use and cannot be re-armed by a re-render. Held here
  // because both the scope resolver (which READS them) and the thread loader
  // (which CONSUMES them) need the same two.
  const startNewRef = useRef(!!startNew);
  const linkedThreadRef = useRef<string | null>(initialThreadId ?? null);
  useEffect(() => { linkedThreadRef.current = initialThreadId ?? null; }, [initialThreadId]);
  useEffect(() => { if (startNew) startNewRef.current = true; }, [startNew]);

  // Take `?new=1` back out of the URL once acted on, so a refresh is governed by
  // the persisted selection instead of blanking a thread that now exists.
  const stripNewParam = useCallback(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (!url.searchParams.has("new")) return;
    url.searchParams.delete("new");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }, []);

  const scopeState = useChatScope({
    contextKey, effectiveMode, scoped, initialThreadId, startNew,
    startNewRef, linkedThreadRef, onError: setErr,
  });
  const {
    scope, selectedModel, setSelectedModel, setSelectedEffort, setSelectedPermissionMode,
  } = scopeState;

  // The queue must know how to send, and the sender must know the queue. One of
  // the two has to be reached through a ref; the send side is the one that
  // changes identity, so it is the ref.
  const sendBatchRef = useRef<
    (
      text: string,
      parts: { text: string; sentAt: string; clientId: string }[],
      opts?: { fromQueue?: boolean },
    ) => Promise<boolean>
  >(async () => false);
  const [compacting, setCompacting] = useState(false);
  // "May the queue send now" is computed near the bottom of this component, from
  // state that is only available once the store and the send pipeline exist. The
  // boolean is relayed as state (so a change re-triggers the queue's drain) and
  // the ref carries the value as of THIS render (so the drain cannot act on a
  // stale `true`). See useMessageQueue.
  const [canDrain, setCanDrain] = useState(false);
  const canDrainRef = useRef(false);
  // Stop hands the wheel back, so the queue stops draining ITSELF until the
  // operator sends again. Without this the queue's self-drain (it fires the
  // moment nothing is running, and a stop makes that moment) would send
  // everything they had typed ahead the instant they stopped the turn. Nothing
  // is dropped: the messages stay queued and on screen. See canDrainQueue.
  //
  // The hold belongs to the QUEUE, stored beside it and keyed by thread, so it
  // is exactly as durable as the messages it is holding. It was component state
  // for an hour and in that hour a reload or a thread switch dropped the hold,
  // kept the queue, and fired the lot as an unattended turn.
  const queue = useMessageQueue({
    scope,
    canDrain,
    canDrainNow: () => canDrainRef.current,
    // `fromQueue`: these messages are still in the queue's durable storage, and
    // the queue takes them back if the server refuses them.
    send: (t, parts) => sendBatchRef.current(t, parts, { fromQueue: true }),
  });

  const thread = useChatThread({
    scope,
    providersReady: scopeState.providersReady,
    variant,
    queue,
    startNewRef,
    linkedThreadRef,
    initialThreadId,
    stripNewParam,
    setSelectedModel,
    setSelectedEffort,
    setSelectedPermissionMode,
    onError: setErr,
    onLeaveForList: () => navigate("/chats"),
  });
  const { activeId, activeIdRef } = thread;

  // The conversation itself: one store, fed by one endpoint from a cursor.
  const log = useThreadLog({ threadId: activeId, onError: setErr });
  const { state, stateRef, dispatch, transcript, sync, runActive, hydrated, staleSince } = log;
  const runActiveRef = useRef(runActive);
  runActiveRef.current = runActive;

  const [taskRefetchKey, setTaskRefetchKey] = useState(0);

  const stream = useTurnStream({
    dispatch, stateRef, sync,
    onTurnEnd: () => {
      void thread.refreshThreads();
      thread.scheduleAutoNameRefetch(wasFirstExchange(stateRef.current));
      setTaskRefetchKey((k) => k + 1);
      // The finished turn applied a provider action (e.g. an app persona editing
      // a budget plan); it rides in the committed message's meta.notes.
      const msgs = committedMessages(stateRef.current);
      if (msgs[msgs.length - 1]?.meta?.notes?.length) onAction?.();
    },
    onError: setErr,
    onDrainQueue: () => queue.dispatch(),
  });

  // STOP ENDS THE RUN. It used to close this tab's socket and nothing else: the
  // turn kept working and kept spending, and the UI looked idle only because a
  // local flag said so. The indicator now reads liveness from the server, so
  // that arrangement showed itself — "Still running", right after Stop.
  //
  // The server call comes first and is awaited: it returns once the run has
  // committed whatever it produced, so the socket closes onto a transcript that
  // already holds the partial reply. A failure still closes the socket, because
  // leaving the operator holding a button that did nothing visible is worse than
  // the error line beside it.
  const { beginStop, stopWatching } = stream;
  const onStopTurn = useCallback(async () => {
    const runId = stateRef.current.activeRun?.runId ?? null;
    // Claim the ending before the request goes out: the `done` frame arrives
    // mid-flight and its handler would retire the turn and drain the queue.
    beginStop();
    queue.hold();
    try {
      if (runId) await chatApi.stopTurn(runId);
    } catch (e) {
      setErr(`Could not stop the turn: ${String(e)}`);
    } finally {
      stopWatching();
    }
  }, [beginStop, stateRef, stopWatching, queue]);

  // /compact reaches BOTH homes of a chat's context — see useCompaction.
  const { ctxNonce, compact, queuedStatus, clear } = useCompaction({
    activeIdRef, runActiveRef, queue, dispatch,
    setCompacting, setErr, sync,
    sendTurn: (t, clientId) => sendBatchRef.current(t, [{ text: t, sentAt: new Date().toISOString(), clientId }]),
  });

  // ---- Voice mode ----------------------------------------------------------
  // A mode of THIS chat, not a separate screen — see useVoiceMode.
  const {
    voiceOn, toggleVoice, settingsOpen: voiceSettings, setSettingsOpen: setVoiceSettings,
    orbRef, vsession, vsnap, vprefs, vupdatePrefs,
  } = useVoiceMode({
    contextKey,
    model: selectedModel,
    activeId,
    onAdoptThread: (id) => {
      if (!activeIdRef.current) thread.adoptThreadId(id);
      void thread.refreshThreads();
    },
    onFatal: setErr,
  });

  // A finished spoken exchange stays on screen until its persisted messages
  // land — see useVoiceSettle.
  const { settling } = useVoiceSettle({
    voiceOn, activeId, vsnap,
    committedCount: () => committedMessages(stateRef.current).length,
    sync,
    refreshThreads: () => { void thread.refreshThreads(); },
  });

  // Following the conversation, composer sizing, and jump-to-latest are one
  // behaviour — see useLogScroll for the rules and the bugs each prevents.
  const [input, setInput, clearInput] = useDraft(contextKey);
  const [inputFocused, setInputFocused] = useState(false);
  // The voice snapshot is in `followDeps` because ChatLog renders the spoken
  // turn from it directly, not from the transcript — without it the log stops
  // following mid-utterance, which is the one time the operator is not touching
  // the screen to scroll it themselves.
  const {
    scrollRef, composerRef, inputRef, showJumpToLatest, onLogScroll, followLatest,
  } = useLogScroll({
    input,
    inputFocused,
    followDeps: [transcript, queue.items, vsnap.state, vsnap.sentences, vsnap.lastUser],
    hasContent: transcript.length > 0,
  });

  const pipeline = useSendPipeline({
    scope, selectedModel, selectedEffort: scopeState.selectedEffort,
    selectedPermissionMode: scopeState.selectedPermissionMode,
    chatModeRef: thread.chatModeRef, activeIdRef,
    adoptThread: thread.adoptThread,
    stateRef, dispatch, sync, queue,
    compacting, compact, clear, setErr,
    onSend: () => {
      // A note describes the LAST thing the operator did. Sending makes it
      // stale, so it goes with the error it sits beside.
      setNotice(null);
      followLatest();
      // They are driving again, so the queue may resume draining itself.
      queue.release();
    },
  });
  sendBatchRef.current = pipeline.sendBatch;

  // ONE answer to "may the queue send now", shared by the queue and the send
  // path so they cannot disagree about whether the chat is busy.
  const drainable = canDrainQueue({
    compacting, starting: pipeline.starting, runActive, loadingThread: thread.loadingThreads,
    heldByStop: queue.held,
  });
  canDrainRef.current = drainable;
  useEffect(() => { setCanDrain(drainable); }, [drainable]);

  // RE-ATTACH, in one place. If the server says a run is in flight on this
  // thread and this tab has no socket for it, open one. That covers every way a
  // run can turn up: started here, started in another tab or on another device,
  // adopted after a backend restart, resumed after a reload, or taken over by a
  // fresh run when the old one refused a delivery. Liveness is server state; a
  // local flag can only ever describe this tab, which is why the old
  // reattach-on-poll + reattach-on-send + reattach-on-drop paths each had their
  // own copy of this rule and each got it subtly different.
  const runId = state.activeRun?.runId ?? null;

  // Answer a tool-permission request from a turn. The turn is BLOCKED on stdin
  // until this lands, so a failure here has to be visible: a silent catch would
  // leave the operator looking at a chat that had simply stopped.
  //
  // No optimistic update. The segment moves when the server says the frame
  // reached the process (an SSE permission_resolved, or the refresh below) —
  // showing "Allowed" for an answer that never landed would be the one lie this
  // control cannot afford.
  const answerPermission = useCallback(async (
    turnId: string,
    a: { requestId: string; allow: boolean; message?: string; applySuggestion?: boolean },
  ) => {
    if (!activeIdRef.current) return;
    try {
      await chatApi.answerPermission(activeIdRef.current, turnId, {
        request_id: a.requestId,
        behavior: a.allow ? "allow" : "deny",
        ...(a.message ? { message: a.message } : {}),
        ...(a.applySuggestion ? { apply_suggestion: true } : {}),
      });
      // "Stop asking" changes the chat's mode server-side; pull the new value in
      // rather than guessing it, so the composer's picker and the server agree.
      if (a.applySuggestion) {
        const t = await chatApi.getThread(activeIdRef.current).catch(() => null);
        if (t) setSelectedPermissionMode(t.permission_mode ?? "");
      }
    } catch (e) {
      setErr(`Couldn't answer that request: ${e}`);
    }
  }, [activeIdRef, setErr, setSelectedPermissionMode]);
  const { attach, hasOpenStream, isDetached, closeStream } = stream;
  useEffect(() => {
    // Keyed on the run's ID, not the run object: that object changes on every
    // streamed segment, and re-running this hundreds of times a turn to
    // early-return is not free.
    if (!runId || !activeId) return;
    if (hasOpenStream() || isDetached(runId)) return;
    attach(runId);
  }, [runId, activeId, attach, hasOpenStream, isDetached]);

  // ...AND THE SAME RULE ON A TIMER, because losing a socket changes none of
  // the deps above. `attach`/`hasOpenStream`/`isDetached` are stable callbacks
  // and the run's id does not change when its stream dies, so that effect
  // cannot re-run for the rest of the run: a turn that exhausted its reconnect
  // budget stayed unwatched forever, its `finish()` never ran, and with it went
  // refreshThreads, the auto-name refetch, the task refetch and the action
  // notes. A first-exchange chat kept its placeholder title until a reload.
  //
  // This is the deleted `useTurnLiveness`, restored: the question "is a run in
  // flight" is answered by the SERVER (`active_run`, kept current by the log
  // poll), and whenever the answer is yes and this tab holds no socket, this
  // re-attaches. Reading the store through the ref means the interval does not
  // re-render the chat while a turn streams.
  useEffect(() => {
    if (!runId || !activeId) return;
    const t = window.setInterval(() => {
      const live = stateRef.current.activeRun?.runId;
      if (!live || hasOpenStream() || isDetached(live)) return;
      attach(live);
    }, LIVENESS_MS);
    return () => window.clearInterval(t);
  }, [runId, activeId, attach, hasOpenStream, isDetached, stateRef]);

  // Leaving a thread closes its socket. The store is reset by useThreadLog.
  useEffect(() => () => closeStream(), [activeId, closeStream]);

  // The committed messages, flat. Read by the context meter's local estimate and
  // by the egress banner; nothing renders from it (the transcript is `blocks`).
  const messages = useMemo(() => committedMessages(state), [state]);

  // ---- Drawers, pickers, mentions ------------------------------------------
  const [drawer, setDrawer] = useState<ChatDrawer>(null);
  const inboxOpen = drawer === "inbox";
  const tasksOpen = drawer === "tasks";
  const [inboxCount, setInboxCount] = useState(0);
  const [openTaskCount, setOpenTaskCount] = useState(0);
  const [ticketsOpen, setTicketsOpen] = useState(false);
  const [slashSel, setSlashSel] = useState(0);
  const [mentionSel, setMentionSel] = useState(0);
  // The slash BUTTON opens the command list as a menu. It is deliberately
  // separate from "the operator is typing a /command": the button used to work
  // by writing "/" into the composer, which meant tapping it destroyed whatever
  // had been typed. A picker must never edit the thing you were writing.
  const [slashOpen, setSlashOpen] = useState(false);
  const slug = buildSlug(contextKey);
  const taskScope = taskScopeFor(contextKey);

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

  const send = useCallback(() => {
    const text = input.trim();
    if (!text) return;
    // clearInput, not setInput(""): a send-clear must be written through to
    // every synced surface immediately and then defended, or a stale copy from
    // another tab/device can restore the text you just sent.
    clearInput();
    // TYPED TEXT IS A CHAT MESSAGE, in voice mode as much as out of it.
    //
    // It used to be handed to the voice engine whenever voice was on, and that
    // engine is not this chat: a separate resident process with its own system
    // prompt, NO tools, its own cwd, and a session that is never given this
    // thread's transcript. So typing while the mic was on quietly answered from
    // a different place, could not do anything, and spent tokens that never
    // reached the usage ledger. Voice INPUT still goes the voice path — that is
    // what the mode is for.
    if (voiceOn) vsession.setTyping(false);
    pipeline.submitText(text);
  }, [input, clearInput, voiceOn, vsession, pipeline]);

  // ---- Slash commands ------------------------------------------------------
  // Two kinds:
  //   • "local"       - a Workshop chat action handled here (new chat, etc.).
  //   • "passthrough" - a Claude Code command (/compact, /cost, /context, …)
  //                     forwarded RAW to the agent's claude session, where the
  //                     CLI executes it natively. Any "/x" that isn't listed
  //                     still passes through when sent.
  const build = effectiveMode === "build";
  const popOut = useCallback(() => navigate(`/chat/${encodeURIComponent(contextKey)}`), [navigate, contextKey]);
  const slashCommands = buildSlashCommands({
    isBuild: build,
    hasThread: !!activeId,
    hasTaskScope: !!taskScope,
    canExpand: variant !== "full" && !hideExpand,
    compacting,
    actions: {
      newChat: () => thread.newChat(),
      deleteChat: () => { if (activeId) thread.setConfirmDeleteId(activeId); },
      toggleTasks: () => setDrawer((d) => (d === "tasks" ? null : "tasks")),
      toggleInbox: () => setDrawer((d) => (d === "inbox" ? null : "inbox")),
      popOut,
      showHelp: () => setSlashOpen(true),
      openDeploys: () => {
        const p = projectSlugFor(contextKey);
        navigate(p ? `/projects/${encodeURIComponent(p)}#deploys` : "/deploys");
      },
      compact: () => void compact(),
      clear: () => void clear(),
    },
  });
  const { typed: slashTyped, matches: slashMatches, show: showSlash } =
    matchSlash(input, slashCommands, slashOpen);
  const { matches: mentionMatches, show: showMentions } = matchMention(input, mentionable);

  const pickMention = (s: string) => {
    setInput(applyMention(input, s));
    setMentionSel(0);
    inputRef.current?.focus();
  };
  const runSlash = (c: SlashCommand) => {
    // Only clear the composer when its contents WERE the command being typed.
    // Picking from the button-opened menu leaves your draft exactly as it was.
    if (slashTyped) setInput("");
    setSlashOpen(false);
    setSlashSel(0);
    if (c.kind === "passthrough") { pipeline.submitText(`/${c.name}`); return; }
    c.run?.();
  };

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

  const back = () => {
    // Prefer where you actually came from; otherwise the chat list, which is
    // this surface's parent now that the thread dropdown is gone.
    if (window.history.length > 1) navigate(-1);
    else navigate("/chats");
  };

  // ---- The live turn's visible state --------------------------------------
  // A RUN IN FLIGHT IS ALWAYS VISIBLE, and it is visible for as long as the
  // SERVER says it is running — not for as long as this tab holds a socket. The
  // elapsed clock is anchored to the run's real start, so a reload mid-turn
  // shows the true duration and a reconnect does not restart it.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!runActive) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    setNowMs(Date.now());
    return () => window.clearInterval(id);
  }, [runActive]);
  const elapsed = elapsedSeconds(state.activeRun?.startedMs ?? null, nowMs);
  const workingLabel = turnLabel({
    attached: stream.attached,
    reconnecting: stream.reconnecting,
    compacting: pipeline.turnIsCompact,
  });

  // ---- Egress banner (build only) -----------------------------------------
  const lastErrorMessage = useMemo(() => {
    const msgs = messages;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].meta?.error) return msgs[i].content;
    }
    return null;
  }, [messages]);
  const egressDetail = isEgressFailure(err)
    ? err
    : isEgressFailure(lastErrorMessage)
      ? lastErrorMessage
      : null;
  const showEgressBanner = effectiveMode === "build" && egressDetail != null;

  // Re-read on every render rather than on a timer: the chat re-renders often
  // enough that the elapsed figure stays honest, and a timer whose only job is
  // to age a string is a subscription that outlives the condition it describes.
  const staleNote = staleNoticeText(staleSince, Date.now());

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

  // ---- In-chat cards -------------------------------------------------------
  const recordCardState = useCallback(
    (key: string, cardState: { phase: string; note?: string | null; slug?: string | null; title?: string | null }) => {
      thread.setCardStates((prev) => ({ ...prev, [key]: cardState }));
      const id = activeIdRef.current;
      if (!id) return;
      void chatApi.setCardState(id, key, cardState as CardState)
        // The server's map is the authority, not the optimistic patch. Taking
        // the response back is what keeps a card that has been acted on from
        // re-offering "File and run" after a reload.
        .then((r) => { if (r?.card_states) thread.setCardStates(r.card_states); })
        .catch((e) => {
          // Previously swallowed with a note that it would "re-sync on the next
          // action" — it never did. The optimistic state simply diverged: the
          // card looked filed, the server had no record, and the next load
          // brought back an un-acted card for work that had already run.
          setErr(`Couldn't record that action on the server: ${e}. Reload to see the real state of this card.`);
        });
    },
    [thread.setCardStates, activeIdRef],
  );
  const cardCtx = useMemo<UiCardCtx>(
    () => ({
      navigate, contextKey, threadId: activeId, rescopeThread: chatApi.rescopeThread,
      cardStates: thread.cardStates, recordCardState,
    }),
    [navigate, contextKey, activeId, thread.cardStates, recordCardState],
  );

  // ---- Chrome --------------------------------------------------------------
  const toolbar = (
    <ChatToolbar
      variant={variant}
      mode={effectiveMode}
      slug={slug}
      hideExpand={hideExpand}
      activeId={activeId}
      activeThreadTitle={thread.activeThreadTitle}
      showProviderPicker={variant === "full" || effectiveMode === "data"}
      providers={scopeState.providers}
      activeProvider={scopeState.activeProvider}
      onChooseProvider={scopeState.chooseProvider}
      chatMode={thread.chatMode}
      onChooseChatMode={thread.chooseChatMode}
      drawer={drawer}
      onSetDrawer={setDrawer}
      inboxCount={inboxCount}
      taskScope={taskScope}
      openTaskCount={openTaskCount}
      onBack={back}
      onOpenWorkbench={() => navigate(`/agents/${slug}/chat`)}
      onFileTicket={() => setTicketsOpen(true)}
      onEditTitle={() => thread.setRenameOpen(true)}
      onNewChat={() => thread.setConfirmNew(true)}
      onDelete={thread.setConfirmDeleteId}
      onPopOut={popOut}
      onNavigate={navigate}
    />
  );

  const dialogs = (
    <ChatDialogs
      scope={scope}
      activeId={activeId}
      activeThreadTitle={thread.activeThreadTitle}
      ticketsOpen={ticketsOpen}
      onCloseTickets={() => setTicketsOpen(false)}
      onFiled={setNotice}
      renameOpen={thread.renameOpen}
      renameBusy={thread.renameBusy}
      onCancelRename={() => thread.setRenameOpen(false)}
      onSaveRename={thread.saveRename}
      confirmNew={thread.confirmNew}
      onCancelNew={() => thread.setConfirmNew(false)}
      onConfirmNew={() => { thread.setConfirmNew(false); thread.newChat(); }}
      confirmDeleteId={thread.confirmDeleteId}
      confirmDeleteTitle={thread.confirmDeleteId ? thread.threads.find((t) => t.id === thread.confirmDeleteId)?.title : undefined}
      onCancelDelete={() => thread.setConfirmDeleteId(null)}
      onConfirmDelete={async (id) => { thread.setConfirmDeleteId(null); await thread.removeThread(id); }}
    />
  );

  // A small pill floating over the log, not a bar that takes its own row.
  //
  // `onMouseDown` preventDefault: with the mobile keyboard open, tapping this
  // (mousedown) blurs the composer BEFORE the click fires — the keyboard closing
  // mid-gesture resizes the viewport under the operator's finger, so the tap
  // lands somewhere else and the click never registers, costing a second tap.
  const jumpToLatest = showJumpToLatest ? (
    <button className="chat-jump" onMouseDown={(e) => e.preventDefault()} onClick={followLatest}>
      <ArrowDown size={14} aria-hidden /> Jump to latest
    </button>
  ) : null;

  // `loading` is BOTH loads, not just the thread list. `loadingThreads` goes
  // false the moment a thread is selected, which is before the transcript's own
  // first page is even requested; on its own it let the empty state render over
  // an open conversation for a full round trip. That is "my chat opens blank".
  // ONE BUBBLE PER MESSAGE. A send the server refused is written to the durable
  // queue so it survives a reload (the reducer's `pending` does not), but while
  // THIS tab still holds the optimistic copy both would draw — the same text,
  // twice, each with its own Retry. The store's copy wins for as long as it
  // exists; the queue's copy is what is left afterwards.
  const queuedForDisplay = useMemo(
    () => queue.items.filter((q) => !state.pending[q.clientId]),
    [queue.items, state.pending],
  );

  const logView = (
    <ChatLog
      scrollRef={scrollRef}
      onScroll={onLogScroll}
      loading={thread.loadingThreads || !hydrated}
      error={err}
      blocks={transcript}
      isBuild={build}
      inFlight={runActive || pipeline.starting}
      selectedModel={selectedModel}
      cardCtx={cardCtx}
      onRetry={pipeline.retry}
      onRetryQueued={queue.retryFailed}
      workingLabel={workingLabel}
      elapsed={elapsed}
      answerPermission={answerPermission}
      liveTurnId={runId}
      queued={queuedForDisplay}
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

  const composer = (
    <ChatComposer
      composerRef={composerRef}
      inputRef={inputRef}
      input={input}
      onChange={(v) => {
        setInput(v);
        // In voice mode, keep the engine's typing-hold aware of whether the
        // composer currently holds text (so it defers taking its turn).
        if (voiceOn) vsession.setTyping(v.trim().length > 0);
      }}
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
      streaming={runActive}
      onSend={send}
      onStop={() => { void onStopTurn(); }}
      canSend={!!input.trim()}
      modelOptions={scopeState.modelOptions}
      selectedModel={selectedModel}
      onSelectModel={setSelectedModel}
      effortLevels={scopeState.effortLevels}
      effectiveEffort={scopeState.effectiveEffort}
      onSelectEffort={setSelectedEffort}
      modeOptions={scopeState.modeOptions}
      effectivePermissionMode={scopeState.effectivePermissionMode}
      onSelectPermissionMode={setSelectedPermissionMode}
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
      {taskPanel}
      <div className="chat-log-wrap">
        {/* Floats over the top of the log instead of pushing it down — it used
            to sit in flow and shove the whole transcript downward every time an
            agent raised something, which shifted the log out from under wherever
            the operator was reading right as a new item arrived. */}
        <InboxStrip
          chatId={activeId}
          open={inboxOpen}
          onCountChange={setInboxCount}
          onClose={() => setDrawer(null)}
          onOpenGlobal={() => navigate("/work?tab=inbox")}
        />
        {logView}
        {jumpToLatest}
      </div>
      {egressBanner}
      {/* Not gated on message count. It used to require `messages.length > 0`,
          which silently swallowed every error on a fresh chat — including the
          model-list load failure, which happens exactly there.

          WHAT IS LEFT IN THIS SLOT is only what belongs to the CHAT rather than
          to a message: the thread list, the model list, a failed delete, a
          failed stop. Anything caused by a message the operator sent renders
          under that message now (see UnsentNote), which is the log-placement
          rule in frontend/CLAUDE.md and is why this could be one slot at all —
          it used to also carry every send failure, overwritten by the next one.
          The cap is a bound on a pathological body, not a summary: every writer
          here passes a sentence, so it should never fire. */}
      {err && !showEgressBanner && <div className="chat-err">{clampError(err)}</div>}
      {/* THIS BROWSER HAS STOPPED HEARING THE BACKEND. It belongs in this slot
          and not in the log, because it is a fact about the connection rather
          than about any message: nothing in the transcript is wrong, it has
          simply stopped arriving. It used to be reported only when the
          transcript was EMPTY, so the case that matters — a full chat quietly
          frozen — was the one case that said nothing. */}
      {staleNote && <div className="chat-err">{staleNote}</div>}
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
