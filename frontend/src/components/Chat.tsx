import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { useNavigate } from "react-router-dom";
import { WifiOff, ArrowDown, Paperclip, Unplug } from "lucide-react";
import { Notice } from "@workshop/ui";
import WorkDrawer from "./chat/WorkDrawer";
import { FiledBar, type FiledNotice } from "./chat/FiledNotice";
import ChatComposer from "./chat/ChatComposer";
import { useAttachments, dragHasFiles, filesFromTransfer } from "./chat/useAttachments";
import { canSendWith } from "./chat/attachments";
import ChatToolbar from "./chat/ChatToolbar";
import ChatLog from "./chat/ChatLog";
import BranchAction from "./chat/BranchAction";
import BranchPicker from "./chat/BranchPicker";
import ChatProblemBlock from "./chat/ChatProblemBlock";
import ChatDialogs from "./chat/ChatDialogs";
import ContextModal from "./chat/ContextModal";
import { useCompaction, type FoldMode } from "./chat/useCompaction";
import { keepFocusOnTap } from "./chat/keepFocus";
import { useVoiceMode } from "./chat/useVoiceMode";
import { matchMention, applyMention } from "./chat/mentions";
import { enterAction } from "./chat/enterKey";
import { usePhysicalKeyboard } from "./chat/usePhysicalKeyboard";
import { type ChatDrawer } from "./chat/drawers";
import {
  deriveMode, buildSlug, taskScopeFor, composerPlaceholder,
  isEgressFailure, type ChatMode, type ChatVariant,
} from "./chat/chatScope";
import { useLogScroll } from "./chat/useLogScroll";
import { spliceDictation, dictationLost, typedPortion } from "./chat/voiceDictation";
import { useTurnStream } from "./chat/useTurnStream";
import { useThreadLog } from "./chat/useThreadLog";
import { useSendOutbox, type SendOutbox } from "./chat/useSendOutbox";
import { useChatScope } from "./chat/useChatScope";
import { useChatThread } from "./chat/useChatThread";
import { useSendPipeline, wasFirstExchange } from "./chat/useSendPipeline";
import { logPlaceholder } from "./chat/chatLogState";
import { placementFor, type ChatProblem } from "./chat/chatProblem";
import { scopeSwitch } from "./chat/scopeSwitch";
import { STALE_TEXT } from "./chat/staleNotice";
import { elapsedSeconds, turnLabel } from "./chat/turnStatus";
import {
  committedClientIds, committedMessages, withoutCommitted,
} from "./chat/transcriptBlocks";
import { chatApi, type CardState } from "../lib/chat";
import { writeSelection, NEW_CHAT } from "../lib/chatSelection";
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
//   useSendOutbox     durability for sends the server has not taken
//   useCompaction     /compact and /clear, both one server call and no turn
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
  // THE CHAT'S ONE PROBLEM SLOT. Everything in it is chat-scoped by definition —
  // anything caused by a specific message is said under that message instead
  // (see chat/chatProblem for the routing rule, chat/UnsentNote for the note).
  // It carries the endpoint that failed and, where retrying is a real action, a
  // retry, because that is what the error state owes the operator.
  const [err, setErr] = useState<ChatProblem | null>(null);
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

  // WHICH fold is in flight, not merely whether one is. Null while none is
  // running. Everything that used to read a boolean still does (`compacting`),
  // but the log needs the mode to say the true thing on its in-flight line.
  const [folding, setFolding] = useState<FoldMode | null>(null);
  const compacting = folding !== null;

  // The outbox needs the store, and the thread loader needs the outbox (it is
  // what points storage at the conversation being opened). The store is built
  // below, from `activeId`, so one of the three has to be reached through a
  // ref: this one, because it is the only handle whose identity is stable for
  // the life of the component.
  const outboxRef = useRef<SendOutbox>({
    bind: () => {}, carryToThread: () => {}, resetToBlank: () => {}, forget: () => {},
  });

  const thread = useChatThread({
    scope,
    providersReady: scopeState.providersReady,
    variant,
    outboxRef,
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
  const { state, stateRef, dispatch, transcript, sync, runActive, hydrated, stale, branch } = log;
  // Sends this browser has made that the server has not taken. The ONLY chat
  // state the browser still owns on its own, and it owns it because the server
  // has never heard of it: a request that did not land. Everything else — the
  // transcript, the run in flight, the queue of messages waiting on it — is
  // read from the server. See useSendOutbox.
  outboxRef.current = useSendOutbox({ scope, state, dispatch });
  const runActiveRef = useRef(runActive);
  runActiveRef.current = runActive;

  const [taskRefetchKey, setTaskRefetchKey] = useState(0);

  // Changing the scope MOVES this conversation. It does not walk away from it.
  //
  // The picker used to only set `activeProvider`, and the thread list is
  // fetched per scope (`/api/chat/threads?context=<scope>`), so switching
  // re-listed a different scope's threads, found none in a fresh scope, and
  // selected a blank chat. From the operator's side the conversation he was
  // having vanished. He reported it as a design flaw twice, most recently on
  // first opening the `development` scope, and he is right: the scope is a
  // property OF the chat, so changing it should re-bind the chat, not navigate
  // away from it. chat-scope-design-2026-07-18.md §397 says the same thing, and
  // POST /threads/:id/rescope already does exactly the right work (keeps the id
  // and the transcript, moves context_key, drops the live session so the next
  // turn re-assembles with the new persona). Nothing called it.
  //
  // An empty chat has nothing to move, so it just switches. A chat with a run
  // in flight refuses: rescope clears the session the run is speaking through,
  // and doing that underneath a live turn breaks it. Saying so is better than
  // either corrupting the run or silently ignoring the click.
  const chooseScope = useCallback(async (key: string) => {
    const id = activeIdRef.current;
    const plan = scopeSwitch({
      target: key,
      current: scopeState.scope,
      hasHistory: !!id && stateRef.current.order.length > 0,
      runActive: runActiveRef.current,
    });
    if (plan.action === "noop") return;
    if (plan.action === "switch") {
      // There is nothing to move, but useChatThread's loader re-resolves on
      // every scope change, and its fallback is `readSelection(scope)` — the
      // last thread that was open in the TARGET scope. Left unset, moving a
      // blank draft to `development` silently swapped in whatever dev
      // conversation was open last time, discarding what was being typed.
      // Recording the blank intent for the new scope keeps the loader landing
      // on blank there too, same as pressing "New chat" does today.
      writeSelection(key, NEW_CHAT);
      scopeState.chooseProvider(key);
      return;
    }
    if (plan.action === "refuse") {
      setErr({ text: plan.reason });
      return;
    }
    // `rescope` is only planned when the thread exists; this narrows the type
    // and is the one place that could go stale if the rule ever changes.
    if (!id) return;
    try {
      await chatApi.rescopeThread(id, key);
      // Claim the selection under the NEW scope before switching, or the
      // thread loader reads that scope's remembered selection and opens
      // something else.
      writeSelection(key, id);
      scopeState.chooseProvider(key);
      void thread.refreshThreads();
      void sync();
    } catch (e) {
      setErr({
        text: `Couldn't move this chat to ${key}: ${e}`,
        endpoint: `/api/chat/threads/${id}/rescope`,
      });
    }
    // scopeState and thread are stable hook objects; the refs are refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeState.scope, scopeState.chooseProvider, activeIdRef, stateRef, runActiveRef, sync]);

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
  // Stop is not instant: the server reaches the run, signals its process group
  // and commits what it produced, and only then answers. Until this turn ends
  // the indicator said "Working" and the button looked untouched, so a press
  // read as having done nothing and invited a second one.
  const [stopping, setStopping] = useState(false);
  const onStopTurn = useCallback(async () => {
    const runId = stateRef.current.activeRun?.runId ?? null;
    // Claim the ending before the request goes out: the `done` frame arrives
    // mid-flight and its handler would retire the turn.
    //
    // THE HOLD IS THE SERVER'S. Stopping ends the run, and the server drains
    // the thread's queue whenever a run ends — so the stop endpoint holds the
    // queue in the same request, and the operator's next send releases it.
    // Holding it here as well would be a second owner of the same fact, and
    // this one would not survive a reload.
    beginStop();
    setStopping(true);
    try {
      if (runId) await chatApi.stopTurn(runId);
    } catch (e) {
      setErr({
        text: `Could not stop the turn: ${String(e)}. It may still be running; press Stop again.`,
        endpoint: `/api/chat/turns/${runId ?? "<run>"}/stop`,
      });
    } finally {
      stopWatching();
      // Cleared whatever happened. On success the run is gone and the indicator
      // goes with it; on failure the error line says to press again, and a
      // button still spinning would contradict it.
      setStopping(false);
    }
  }, [beginStop, stateRef, stopWatching]);

  // Stable, because it reaches the memoized MessageList: an inline arrow here
  // turns that memo off on every keystroke (see the note on MessageList's props).
  const onStopClick = useCallback(() => { void onStopTurn(); }, [onStopTurn]);

  // /compact reaches BOTH homes of a chat's context, server-side and without a
  // turn of its own. See useCompaction.
  const { ctxNonce, compact, clear, cancelFold } = useCompaction({
    activeIdRef, setFolding, setErr, sync,
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

  // A SPOKEN MESSAGE IS A MESSAGE. The server now writes it to the thread the
  // moment it is captured, before a word of reply exists, exactly as typing
  // does. So there is nothing to hold on screen and nothing to reconcile: it
  // arrives through the ordinary log like every other message. Pull it in as
  // soon as the server says a turn was taken, rather than waiting out a poll.
  //
  // This replaces useVoiceSettle, which held a COPY of the finished exchange
  // and swapped it for the real messages on a timer. It existed only because
  // voice persisted late, and a copy on screen beside the real thing is what
  // showed the same words twice.
  const lastUserSeen = useRef("");
  useEffect(() => {
    if (!voiceOn || !vsnap.lastUser || vsnap.lastUser === lastUserSeen.current) return;
    lastUserSeen.current = vsnap.lastUser;
    void sync();
    void thread.refreshThreads();
  }, [voiceOn, vsnap.lastUser, sync, thread]);

  // Following the conversation, composer sizing, and jump-to-latest are one
  // behaviour — see useLogScroll for the rules and the bugs each prevents.
  // Draft is stored PER CONVERSATION, not per scope. Keyed by the open thread so
  // switching chats never shows one chat's unsent text in another; a blank chat
  // that has no thread yet keeps its own "new" draft under the scope, which the
  // send-clear (clearInput below) wipes before the thread it creates is adopted.
  const draftKey = activeId ? `${contextKey}::${activeId}` : `${contextKey}::new`;
  const {
    draft: input, setDraft: setInput, clearForSend: clearInput,
    attachments: draftAttachments, setAttachments: setDraftAttachments,
  } = useDraft(draftKey);
  // WHAT YOU ARE SAYING GOES IN THE MESSAGE BOX, because that is where unsent
  // text lives in this app and the box is already mirrored to the server. It
  // used to be a bubble at the foot of the log drawn from socket state, which
  // looked exactly like a saved message and was not one: turning voice off
  // cleared the snapshot and deleted the only copy. Now the words are a draft,
  // so a dropped socket, a closed tab or a turn that never ends leaves them
  // sitting in the composer instead of destroying them.
  //
  // Voice owns a prefix of the box and nothing else. See voiceDictation.ts.
  const dictatedRef = useRef("");
  useEffect(() => {
    if (!voiceOn) { dictatedRef.current = ""; return; }
    const owned = dictatedRef.current;
    const next = vsnap.pendingUser;
    if (owned === next) return;
    if (dictationLost(input, owned)) { dictatedRef.current = ""; return; }
    // TEARDOWN IS NOT A COMMIT. Stopping the session empties the snapshot, which
    // is indistinguishable here from an utterance being taken -- and acting on it
    // would splice the dictated words back out of the box and delete them, which
    // is the exact bug this whole change exists to remove. A session that is not
    // live relinquishes its prefix and leaves the text where it is.
    if (!next && (vsnap.state === "idle" || !vsnap.connected)) {
      dictatedRef.current = "";
      return;
    }
    setInput(spliceDictation(input, owned, next));
    dictatedRef.current = next;
  }, [voiceOn, vsnap.pendingUser, input, setInput]);

  const [inputFocused, setInputFocused] = useState(false);
  // Whether the operator has held the message box open at its tall size. Plain
  // component state on purpose: it is a view preference for the session, not
  // part of the draft, so it must not follow a draft into another thread or be
  // restored days later on a chat you opened to type one line into.
  const [inputExpanded, setInputExpanded] = useState(false);
  // Attached files sit BESIDE the draft, never inside it, which is what makes
  // removing one a single click instead of a run of backspaces. Not keyed per
  // thread like the draft: an upload is already on the server and the list is
  // cleared on send, so there is nothing to restore.
  // THE COMPOSER'S FILES ARE PART OF ITS DRAFT. They are uploaded on arrival, so
  // the bytes were always safe; what was not was the fact that they belong to
  // THIS composer, which lived in component state alone — so a reload brought
  // back the text without the screenshot it was about, and left the file
  // orphaned on the server. They are stored with the text now, under the same
  // key, and restored with it.
  const files = useAttachments({
    scope, threadId: activeId,
    restored: draftAttachments, onChange: setDraftAttachments,
  });
  // The voice snapshot is in `followDeps` because ChatLog renders the spoken
  // turn from it directly, not from the transcript — without it the log stops
  // following mid-utterance, which is the one time the operator is not touching
  // the screen to scroll it themselves.
  const {
    scrollRef, composerRef, inputRef, showJumpToLatest, onLogScroll, followLatest,
  } = useLogScroll({
    input,
    inputFocused,
    expanded: inputExpanded,
    // `folding` is here for the same reason the voice snapshot is: the fold's
    // in-flight line is rendered from it rather than from the transcript, so
    // without it the log does not follow down to the one thing that just
    // appeared at its foot.
    followDeps: [transcript, state.queued, folding, vsnap.state, vsnap.sentences, vsnap.lastUser],
    hasContent: transcript.length > 0,
    // Restore the scroll position per thread when returning to it.
    //
    // AND WHEN THE DROP AREA HANDS THE LOG BACK. A drag unmounts the transcript
    // (the drop area REPLACES it rather than covering it), so the element that
    // comes back afterwards is a new one, scrolled to the top. This hook only
    // restores once per key and its own state survives, since it lives on this
    // component rather than on the log, so nothing would have repositioned it.
    // Dropping the key for the length of the drag makes the return count as
    // re-opening the thread, which is what it now is, and the position saved
    // before the drag is reapplied. Nothing is saved under the null key: the
    // save is guarded on a non-null key, and an unmounted log fires no scroll.
    restoreKey: files.dragging ? null : activeId,
  });

  const pipeline = useSendPipeline({
    scope, selectedModel, selectedEffort: scopeState.selectedEffort,
    selectedPermissionMode: scopeState.selectedPermissionMode,
    chatModeRef: thread.chatModeRef, activeIdRef,
    adoptThread: thread.adoptThread,
    stateRef, dispatch, sync, draftKey,
    compacting,
    // Typed `/compact` and `/clear` ask as well. One rule for the action, not
    // one for the button and another for the keystroke.
    compact: async () => { setConfirmCompact(true); },
    clear: async () => { setConfirmClear(true); },
    setErr,
    onSend: () => {
      // A note describes the LAST thing the operator did. Sending makes it
      // stale, so it goes with the error it sits beside.
      setNotice(null);
      followLatest();
    },
    // `/new` ASKS. It opens the same dialog the toolbar button does rather than
    // abandoning the conversation on a four-character keystroke.
    onNewChat: () => thread.setConfirmNew(true),
  });

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
    const threadId = activeIdRef.current;
    if (!threadId) return;
    try {
      await chatApi.answerPermission(threadId, turnId, {
        request_id: a.requestId,
        behavior: a.allow ? "allow" : "deny",
        ...(a.message ? { message: a.message } : {}),
        ...(a.applySuggestion ? { apply_suggestion: true } : {}),
      });
      // "Stop asking" changes the chat's mode server-side; pull the new value in
      // rather than guessing it, so the composer's picker and the server agree.
      if (a.applySuggestion) {
        const t = await chatApi.getThread(threadId).catch(() => null);
        if (t) setSelectedPermissionMode(t.permission_mode ?? "");
      }
    } catch (e) {
      setErr({
        text: `Couldn't answer that request: ${e}. The turn is still waiting; answer it again.`,
        endpoint: `/api/chat/threads/${threadId}/turns/${turnId}/permission`,
      });
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
  //
  // Paused while hidden, and it runs on the way back: a backgrounded tab has no
  // reason to hold a socket open for a turn nobody is watching, and the run
  // keeps going on the server either way. Returning re-attaches immediately,
  // which is the moment the transcript has to be right.
  usePoll(
    () => {
      const live = stateRef.current.activeRun?.runId;
      if (!live || hasOpenStream() || isDetached(live)) return;
      attach(live);
    },
    LIVENESS_MS,
    { enabled: !!runId && !!activeId, immediate: false },
  );

  // Leaving a thread closes its socket. The store is reset by useThreadLog.
  useEffect(() => () => closeStream(), [activeId, closeStream]);

  // The committed messages, flat. Read by the context meter's local estimate and
  // by the egress banner; nothing renders from it (the transcript is `blocks`).
  // Keyed on the COMMITTED log, not on `state`. This walks and maps every
  // message in the thread, and on `[state]` it did so on every streamed frame,
  // for a list whose contents a frame cannot change.
  const messages = useMemo(
    () => committedMessages(state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.byId, state.order, state.idBySeq],
  );

  // ---- Drawers, pickers, mentions ------------------------------------------
  const [drawer, setDrawer] = useState<ChatDrawer>(null);
  const workOpen = drawer === "work";
  // Inbox items waiting on the operator in this chat. The toolbar badge, and
  // ONLY that: the drawer's ticket zone is ambient, so it does not count here.
  const [needsYouCount, setNeedsYouCount] = useState(0);
  // Stable, because the drawer holds it in an Escape-key effect and in a
  // memoised callback; a fresh lambda per render would re-bind both every time
  // a character is typed.
  const closeDrawer = useCallback(() => setDrawer(null), []);
  const [ticketsOpen, setTicketsOpen] = useState(false);
  // The context breakdown, opened from the meter. It replaced /context, which
  // dumped sixty lines of table into the transcript.
  const [contextOpen, setContextOpen] = useState(false);
  // Compact and clear ASK. Both are irreversible in the way that matters (the
  // model's view of the conversation) and both are one click from a primary
  // button in the context modal.
  const [confirmCompact, setConfirmCompact] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [mentionSel, setMentionSel] = useState(0);
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

  // NOTHING TO RESET. A scope change is a `scope` event in the thread's own log
  // now, so it arrives with that thread and belongs to it — the reason this
  // effect existed (opening another chat kept showing "Scope changed to X", a
  // fact about a thread no longer on screen) cannot happen to a fact that is
  // read from the thread rather than remembered beside it.

  const send = useCallback(() => {
    const text = input.trim();
    // Files with no text is a complete message. `take` hands over what is
    // attached and clears it in the same step, so "sent" and "cleared" can never
    // disagree.
    if (!text && !files.items.some((a) => a.status === "ready")) return;
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
    // The box just emptied, so voice owns nothing in it. Said here rather than
    // inferred a tick later, so the next words dictated go in immediately
    // instead of spending a snapshot relinquishing a prefix that is already
    // gone. Anything dictated was part of `text` and went out with it.
    dictatedRef.current = "";
    pipeline.submitText(text, files.take());
  }, [input, clearInput, voiceOn, vsession, pipeline, files]);

  // ---- Composer helpers ----------------------------------------------------
  // There is no slash catalogue and no picker any more. Three commands are
  // accepted when TYPED (/compact, /clear, /new) and handled in the send
  // pipeline; nothing suggests them. See chat/typedCommands.
  const build = effectiveMode === "build";
  const popOut = useCallback(() => navigate(`/chat/${encodeURIComponent(contextKey)}`), [navigate, contextKey]);

  const { matches: mentionMatches, show: showMentions } = matchMention(input, mentionable);

  // Read on Enter, never rendered. See usePhysicalKeyboard for why it holds no
  // state: this is on the per-keystroke path.
  const physicalKeyboard = usePhysicalKeyboard();

  const pickMention = (s: string) => {
    setInput(applyMention(input, s));
    setMentionSel(0);
    inputRef.current?.focus();
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
    // Enter sends on a real keyboard and starts a new line on a soft one, and
    // the reasoning for both (plus why it is not a width test) is in enterKey.
    // "newline" is the browser's own default for a textarea, so it is reached
    // by NOT calling preventDefault rather than by inserting a "\n" — that way
    // the caret, the undo stack and the auto-grow all behave as they do for any
    // other typed character.
    // `isComposing` is on the NATIVE event: React's synthetic keyboard event
    // does not carry it. Reading it off the synthetic one silently yields
    // undefined, which is how an IME composition ends up being sent.
    const action = enterAction({
      key: e.key,
      shiftKey: e.shiftKey,
      altKey: e.altKey,
      ctrlKey: e.ctrlKey,
      metaKey: e.metaKey,
      isComposing: e.nativeEvent.isComposing,
      keyCode: e.nativeEvent.keyCode,
    }, physicalKeyboard.check());
    if (action === "send") {
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
  //
  // It does not tick while the app is HIDDEN. The clock is derived from the
  // run's start time, so it does not need to be running to be right — it only
  // needs to be re-read when the app comes back, which is what the
  // visibilitychange handler does. Left ungated, this re-rendered the entire
  // chat once a second for the whole length of every turn, including with the
  // PWA in the background, which is where a phone pays for it most.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!runActive) return;
    let id: number | undefined;
    const start = () => {
      if (id === undefined) id = window.setInterval(() => setNowMs(Date.now()), 1000);
    };
    const stop = () => { window.clearInterval(id); id = undefined; };
    const onVisibility = () => {
      setNowMs(Date.now());
      if (document.hidden) stop(); else start();
    };
    setNowMs(Date.now());
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [runActive]);
  const elapsed = elapsedSeconds(state.activeRun?.startedMs ?? null, nowMs);
  const workingLabel = turnLabel({
    attached: stream.attached,
    reconnecting: stream.reconnecting,
    stopping,
  });

  // ---- Egress banner (build only) -----------------------------------------
  // The one failure that arrives ATTACHED TO A MESSAGE and is nonetheless the
  // chat's: the environment blocked the network, so the next message dies the
  // same way and re-sending this one is not the remedy. `placementFor` is that
  // rule, and it is the only thing that decides the escalation.
  const lastFailedMessage = useMemo(() => {
    const msgs = messages;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].meta?.error) {
        return { messageId: msgs[i].meta?.client_id ?? msgs[i].ts, text: msgs[i].content };
      }
    }
    return null;
  }, [messages]);
  const egressDetail =
    err && isEgressFailure(err.text)
      ? err.text
      : lastFailedMessage && placementFor(lastFailedMessage) === "chat-block"
        ? lastFailedMessage.text
        : null;
  const showEgressBanner = effectiveMode === "build" && egressDetail != null;
  // The banner is a richer rendering of ONE failure, so it takes that failure
  // out of the general slot — and only that one. The gate used to be "any
  // banner hides any error", which swallowed an unrelated chat failure (a
  // refused delete, a dead poll) for as long as a blocked-network reply sat in
  // the transcript.
  const chatProblem: ChatProblem | null =
    err && showEgressBanner && egressDetail === err.text ? null : err;

  // No endpoint on this one, deliberately. Rule 9 wants the failing path named
  // where naming it helps someone act; here the only move is Retry, and the
  // path was two thirds of the notice's length. It was part of what made this
  // read as a wall of text over the composer.
  const staleProblem: ChatProblem | null = stale
    ? { text: STALE_TEXT, retry: () => { void sync(); } }
    : null;

  // A line, like every other failure that sits over a working transcript. It
  // was a StateBlock: a centred dashed panel with a 28px icon and an open
  // details block, above the composer, for as long as a blocked-network reply
  // stayed in the log. The raw output is the one part worth keeping behind a
  // disclosure, so it stays there and the rest costs one line.
  const egressBanner = showEgressBanner ? (
    <Notice
      tone="error"
      icon={<WifiOff aria-hidden />}
      className="chat-egress"
    >
      {/* Both sentences in the body, and the disclosure after them: `detail`
          renders after `children`, so putting the advice there would have left
          the Details toggle sitting between the problem and what to do. */}
      External network is blocked in this environment.{" "}
      <span className="ws-notice__detail">
        Run network commands (git clone / npm install) in your own terminal, or
        approve the host in Settings.
      </span>
      <details className="chat-egress__details">
        <summary className="ws-card__meta">Details</summary>
        <pre className="ws-mono chat-egress__pre">{egressDetail}</pre>
      </details>
    </Notice>
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
          setErr({
            text: `Couldn't record that action on the server: ${e}. Reload to see the real state of this card.`,
            endpoint: `/api/chat/threads/${id}/card-state`,
          });
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

  // Going back to a point in the conversation (ADR 0020).
  //
  // Built here rather than inside the transcript because the transcript is
  // memoized on prop identity, and a control that needs the thread id, the
  // branch count and a way to re-sync would otherwise be three more props to
  // keep stable on every bubble. This is one callback whose identity moves only
  // when the branch state actually does, which is rarely and never while typing.
  const onBranchChanged = useCallback(() => { void sync(); }, [sync]);
  const branchThreadId = branch.id ? activeId : null;
  // A bubble whose action would change nothing carries no control at all now
  // (see branchable.ts), so there is no disabled state left to pass down.
  const renderBranch = useCallback((messageId: string, mode: "after" | "before") => (
    branchThreadId
      ? <BranchAction
          threadId={branchThreadId}
          anchorId={messageId}
          mode={mode}
          runActive={runActive}
          onChanged={onBranchChanged}
        />
      : null
  ), [branchThreadId, runActive, onBranchChanged]);
  const branchPicker = branchThreadId
    ? <BranchPicker
        threadId={branchThreadId}
        name={branch.name}
        count={branch.count}
        onChanged={onBranchChanged}
      />
    : null;

  // ---- Chrome --------------------------------------------------------------
  const toolbar = (
    <ChatToolbar
      variant={variant}
      mode={effectiveMode}
      slug={slug}
      hideExpand={hideExpand}
      activeId={activeId}
      activeThreadTitle={thread.activeThreadTitle}
      branchPicker={branchPicker}
      showProviderPicker={variant === "full" || effectiveMode === "data"}
      providers={scopeState.providers}
      activeProvider={scopeState.activeProvider}
      onChooseProvider={chooseScope}
      runActive={runActive}
      chatMode={thread.chatMode}
      onChooseChatMode={thread.chooseChatMode}
      drawer={drawer}
      onSetDrawer={setDrawer}
      needsYouCount={needsYouCount}
      taskScope={taskScope}
      onBack={back}
      onOpenWorkbench={() => navigate(`/agents/${slug}/chat`)}
      onFileTicket={() => setTicketsOpen(true)}
      onEditTitle={() => thread.setRenameOpen(true)}
      // Route rather than select in place. The chat you picked can be bound to
      // any scope, and the loader already reads `?thread=` as a link to fetch by
      // id from wherever it lives (useChatThread, the `linked` branch), so this
      // one path is correct for a chat in this scope and one in another. Same
      // destination the /chats list navigates to.
      onOpenThread={(t) => navigate(
        `/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`,
      )}
      onNewChat={() => thread.setConfirmNew(true)}
      onArchive={(id) => { void thread.archiveThread(id); }}
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
      confirmCompact={confirmCompact}
      onCancelCompact={() => setConfirmCompact(false)}
      onConfirmCompact={() => { setConfirmCompact(false); void compact(); }}
      confirmClear={confirmClear}
      onCancelClear={() => setConfirmClear(false)}
      onConfirmClear={() => { setConfirmClear(false); void clear(); }}
    />
  );

  // A small pill floating over the log, not a bar that takes its own row.
  //
  // `onMouseDown` preventDefault: with the mobile keyboard open, tapping this
  // (mousedown) blurs the composer BEFORE the click fires — the keyboard closing
  // mid-gesture resizes the viewport under the operator's finger, so the tap
  // lands somewhere else and the click never registers, costing a second tap.
  const jumpToLatest = showJumpToLatest ? (
    <button className="chat-jump" onPointerDown={keepFocusOnTap} onClick={followLatest}>
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
  //
  // ...AND NOT AT ALL once the log carries it. A retry reuses the same client
  // id, so a failed send that later lands is committed under the id the durable
  // entry still holds — and dropping the optimistic copy (which reconciliation
  // does the moment the log arrives) is what USED to reveal the durable one. See
  // committedClientIds: the second bubble appeared because the send succeeded.
  //
  // Keyed on the log's own maps, NOT on `state`. `runDelta` builds a new state
  // per streamed token, so `[state]` would rebuild this set dozens of times a
  // second and churn every memo below it — including the transcript one that
  // exists to keep typing from re-rendering the log. Those three references are
  // held stable by the reducer when no event is new; same deps as the committed
  // memo in useThreadLog, for the same reason.
  const committedIds = useMemo(
    () => committedClientIds(state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.byId, state.order, state.idBySeq],
  );
  // WAITING TO BE SENT, as the SERVER reports it. Not this browser's list: the
  // queue is on the thread, so it is the same on every device and it is still
  // there after a reload, a crash, or a change of laptop. Anything the log has
  // since committed is filtered out for the one poll where both can name it.
  const queuedWaiting = useMemo(
    () => withoutCommitted(
      state.queued.map((q) => ({
        clientId: q.client_id, text: q.text, sentAt: q.ts, attachments: q.attachments,
      })),
      committedIds,
    ),
    [state.queued, committedIds],
  );
  //
  // THIS CALLBACK'S IDENTITY IS LOAD-BEARING. It is passed down to MessageList,
  // which is memoized so a keystroke does not re-render the transcript; every
  // other prop it gets is already stable, so if this one churns the memo is off
  // and the whole optimization silently does nothing.
  //
  // It used to depend on `[state.pending, pipeline, queue]`, and two of those
  // change constantly: `state.pending` on every streamed token, and `pipeline`
  // because useSendPipeline returns a bare object literal built from option
  // lambdas that Chat re-creates each render. Reading both through refs is what
  // makes this stable. `pipeline.retry` is itself a stable useCallback, but the
  // ref means this does not depend on that staying true.
  const retryRef = useRef(pipeline.retry);
  retryRef.current = pipeline.retry;
  // ONE RETRY PATH. A failed send is a `pending` entry and nothing else now —
  // the durable copy IS that entry, mirrored to storage — so there is no second
  // kind of failure with a second way to re-send it.
  const onRetryAny = useCallback((clientId: string) => {
    retryRef.current(clientId);
  }, []);

  // Taking a queued message back out, and telling a stopped queue to go. Both
  // are writes to the SERVER's queue: this browser has no copy to fix up, and
  // the sync brings back whatever actually happened.
  const cancelQueued = useCallback((clientId: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    void chatApi.cancelQueued(id, clientId)
      .then(() => sync())
      .catch((e) => setErr({
        text: `Could not unsend that message: ${e}. It is still queued.`,
        endpoint: `/api/chat/threads/${id}/queued/${clientId}/cancel`,
      }));
  }, [activeIdRef, sync, setErr]);
  const releaseQueue = useCallback(() => {
    const id = activeIdRef.current;
    if (!id) return;
    void chatApi.releaseQueue(id)
      .then(() => sync())
      .catch((e) => setErr({
        text: `Could not send the waiting messages: ${e}. They are still queued.`,
        endpoint: `/api/chat/threads/${id}/queued/release`,
      }));
  }, [activeIdRef, sync, setErr]);


  const logLoading = thread.loadingThreads || !hydrated;
  // EXACTLY ONE OF THE TWO renders a given failure. The log owns the error one
  // of its four list states, so when it has nothing else to show, the problem
  // block goes IN it; when the transcript is full of valid history the log is
  // not the place for it, and the slot above the composer takes it instead.
  // Both draw the same component — see ChatProblemBlock.
  const logShowsProblem = logPlaceholder({
    empty: transcript.length === 0,
    loading: logLoading,
    error: !!chatProblem,
    inFlight: runActive || pipeline.starting,
  }) === "error";

  const logView = (
    <ChatLog
      scrollRef={scrollRef}
      onScroll={onLogScroll}
      loading={logLoading}
      error={chatProblem}
      blocks={transcript}
      isBuild={build}
      inFlight={runActive || pipeline.starting}
      selectedModel={selectedModel}
      cardCtx={cardCtx}
      onRetry={onRetryAny}
      onCancelQueued={cancelQueued}
      onReleaseQueue={releaseQueue}
      queueHeld={state.queueHeld}
      queueError={state.queueError}
      workingLabel={workingLabel}
      elapsed={elapsed}
      nowMs={nowMs}
      answerPermission={answerPermission}
      liveTurnId={runId}
      renderBranch={renderBranch}
      branchHead={branch.head}
      queued={queuedWaiting}
      folding={folding}
      onStop={onStopClick}
      stopping={stopping}
      onStopFold={cancelFold}
      voiceOn={voiceOn}
      vsnap={vsnap}
    />
  );

  const composer = (
    <ChatComposer
      composerRef={composerRef}
      inputRef={inputRef}
      input={input}
      onChange={(v) => {
        setInput(v);
        // In voice mode, keep the engine's typing-hold aware of whether the
        // composer currently holds text (so it defers taking its turn).
        if (voiceOn) vsession.setTyping(typedPortion(v, dictatedRef.current).length > 0);
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
      expanded={inputExpanded}
      onToggleExpand={() => setInputExpanded((v) => !v)}
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
      // One rule for "is there anything to send", shared with the Enter key:
      // text, or a finished upload, and never while one is still going up.
      canSend={canSendWith(input, files.items)}
      attachments={files.items}
      attachNotice={files.notice}
      onAttachFiles={files.add}
      onRemoveAttachment={files.remove}
      onDismissAttachNotice={files.dismissNotice}
      onPasteFiles={files.add}
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
      liveCtxTokens={state.activeRun?.ctxTokens}
      voiceOn={voiceOn}
      onToggleVoice={toggleVoice}
      isBuild={build}
      vsnap={vsnap}
      vprefs={vprefs}
      vsession={vsession}
      orbRef={orbRef}
      onUpdatePrefs={vupdatePrefs}
      onOpenVoiceSettings={() => setVoiceSettings(true)}
      onOpenContext={() => setContextOpen(true)}
    />
  );

  // THE WHOLE CHAT IS THE DROP TARGET, transcript and composer alike: the
  // operator asked to drop anywhere in the chat, and a small target you have to
  // aim at is the thing every drag-and-drop implementation gets wrong.
  const panelInner = (
    <div
      className={`chat-panel chat-panel--${variant}${files.dragging ? " is-dropping" : ""}`}
      // ONE PLACE FOR THE WHOLE CHAT. Tapping any control here must not blur the
      // composer, and the guard used to sit on three containers only, so every
      // button in the TRANSCRIPT (copy, branch, approvals, the step and group
      // disclosures, the inbox, the queue note) had none. keepFocusOnTap now
      // asks whether the target is a control, so hoisting it here leaves message
      // text selectable. See keepFocus.ts.
      onPointerDown={keepFocusOnTap}
      onDragEnter={(e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        files.setDragging(true);
      }}
      onDragOver={(e) => {
        // Both are required, and for different reasons: preventDefault is what
        // stops the browser navigating to the dropped file, and dropEffect is
        // what makes the cursor say "copy" rather than "no".
        if (!dragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        // WHERE THE POINTER IS GOING, not how many enters have been counted.
        // This was a depth counter, which cannot survive the swap below: the
        // transcript and composer are REMOVED from the DOM the moment the drag
        // starts, and a removed element's dragleave is not something the spec
        // guarantees, so the count drifted and the zone flickered or stuck.
        // `relatedTarget` is the node being entered; if it is still inside the
        // panel this is a move between children, not a leave. It is null when
        // the pointer leaves the window entirely, which IS a leave.
        const next = e.relatedTarget as Node | null;
        if (next && e.currentTarget.contains(next)) return;
        files.setDragging(false);
      }}
      onDrop={(e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        files.setDragging(false);
        files.add(filesFromTransfer(e.dataTransfer));
      }}
    >
      {toolbar}
      {/* A SWAP, NOT AN OVERLAY. The drop area REPLACES the transcript and the
          composer rather than being laid on top of them, so there is nothing
          showing through behind it (operator, 2026-08-12: an overlay left all
          of it sitting there underneath, which is the wrong thing to build).

          The toolbar stays. It is the chat's chrome rather than its content,
          and dropping a file should not make you lose track of which chat you
          are in. Everything below it is what gets replaced.

          The panel keeps the handlers, so the whole area still takes the drop,
          including the toolbar and the margin around the zone. */}
      {files.dragging ? (
        <div className="chat-dropzone" role="status">
          <div className="chat-dropzone__card">
            <Paperclip size={20} strokeWidth={1.8} aria-hidden />
            <span>Drop to attach</span>
          </div>
        </div>
      ) : (
      <>
      {notice && <FiledBar notice={notice} onDismiss={() => setNotice(null)} />}
      <div className="chat-log-wrap">
        {/* Floats over the top of the log instead of pushing it down — it used
            to sit in flow and shove the whole transcript downward every time an
            agent raised something, which shifted the log out from under wherever
            the operator was reading right as a new item arrived.

            Always mounted, even closed: it owns the inbox poll that feeds the
            toolbar badge. The ticket list inside it is read only while open. */}
        <WorkDrawer
          chatId={activeId}
          scope={taskScope}
          open={workOpen}
          refetchKey={taskRefetchKey}
          onCountChange={setNeedsYouCount}
          onClose={closeDrawer}
          onNavigate={navigate}
        />
        {logView}
        {jumpToLatest}
      </div>
      {egressBanner}
      {/* Not gated on message count. It used to require `messages.length > 0`,
          which silently swallowed every error on a fresh chat — including the
          model-list load failure, which happens exactly there.

          WHAT IS IN THIS SLOT is only what belongs to the CHAT rather than to a
          message: the thread list, the model list, a failed delete, a failed
          stop. Anything caused by a message the operator sent renders under
          that message (see UnsentNote), which is the log-placement rule in
          frontend/CLAUDE.md and the reason this can be one slot at all — it
          used to also carry every send failure, overwritten by the next one.

          It was a bare red div: no endpoint, no retry, and nothing to say
          whether the thing that failed was this message, this thread or the
          backend. It is the same `StateBlock variant="error"` the egress notice
          and the log's error state use now. */}
      {chatProblem && !logShowsProblem && <ChatProblemBlock problem={chatProblem} />}
      {/* THIS BROWSER HAS STOPPED HEARING THE BACKEND. It belongs in this slot
          and not in the log, because it is a fact about the connection rather
          than about any message: nothing in the transcript is wrong, it has
          simply stopped arriving. It used to be reported only when the
          transcript was EMPTY, so the case that matters — a full chat quietly
          frozen — was the one case that said nothing.

          It names the poll that stopped landing and offers the retry, because
          the one thing the operator can usefully do here is ask again. */}
      {staleProblem && <ChatProblemBlock problem={staleProblem} icon={<Unplug aria-hidden />} />}
      {composer}
      </>
      )}
      {dialogs}
      <ContextModal
        open={contextOpen}
        onClose={() => setContextOpen(false)}
        threadId={activeId}
        compacting={compacting}
        onCompact={() => { setContextOpen(false); setConfirmCompact(true); }}
        onClear={() => { setContextOpen(false); setConfirmClear(true); }}
      />
      <VoiceSettings
        open={voiceSettings}
        onClose={() => setVoiceSettings(false)}
        prefs={vprefs}
        updatePrefs={vupdatePrefs}
        session={vsession}
      />
    </div>
  );

  if (variant === "full") {
    return <div className="ws-chat-full">{panelInner}</div>;
  }
  return panelInner;
}
