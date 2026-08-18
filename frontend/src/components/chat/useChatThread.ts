import { useCallback, useEffect, useRef, useState } from "react";
import { chatApi, type CardState, type Thread, type ThreadSummary } from "../../lib/chat";
import { isTransientNetworkError } from "../../lib/httpError";
import {
  NEW_CHAT, forgetThread, readSelection, writeSelection,
} from "../../lib/chatSelection";
import { chatModeDef, type ChatModeId } from "./chatModes";
import type { ChatProblem } from "./chatProblem";
import type { SendOutbox } from "./useSendOutbox";

/** The request `chatApi.listThreads` makes, named so an error block can point at
 *  the thing that actually failed. */
const threadListEndpoint = (scope?: string) =>
  scope ? `/api/chat/threads?context=${scope}` : "/api/chat/threads";

// WHICH CONVERSATION IS OPEN, and the settings that belong to it.
//
// Deliberately NOT the transcript. The transcript is the event log, read from
// one endpoint by useThreadLog; this hook owns the thread list, which thread is
// selected, and the small mutable record that hangs off it (title, model, mode,
// card states). `chatApi.getThread` is called here for exactly those fields and
// its `messages` are never read — a transcript assembled by merging endpoints
// with local state is the anti-pattern behind every reorder bug this chat has
// had.
//
// CHOOSING THE ACTIVE THREAD, in ONE order, from two kinds of input:
//
//   URL intent (a click that just happened) — outranks everything:
//     ?new=1        → blank, and record it under the scope key, which only this
//                     component can compute
//     ?thread=<id>  → that conversation
//   Persisted state (what to do when the URL says nothing):
//     the thread we're already on → stay, never snap to newest
//     NEW_CHAT recorded           → stay blank across reloads
//     the last thread in scope    → restore it
//     otherwise                   → the most recent thread
//
// The link outranking NEW_CHAT is the whole point: a recorded "I want a blank
// chat" is older than the click asking for a specific one. Ranked the other way,
// every chat in the list opened blank once New chat had been pressed; ranked
// with no link intent at all, New chat reopened the thread you had just left.

export type ChatThread = {
  threads: ThreadSummary[];
  activeId: string | null;
  activeIdRef: React.MutableRefObject<string | null>;
  loadingThreads: boolean;
  activeThreadTitle: string | undefined;
  chatMode: ChatModeId;
  chatModeRef: React.MutableRefObject<ChatModeId>;
  chooseChatMode: (next: ChatModeId) => void;
  cardStates: Record<string, CardState>;
  setCardStates: React.Dispatch<React.SetStateAction<Record<string, CardState>>>;
  /** Re-read the thread list. Returns it, so a caller waiting on a
   *  server-side change (the auto-namer) can tell whether it has landed. */
  refreshThreads: () => Promise<ThreadSummary[]>;
  scheduleAutoNameRefetch: (wasFirstExchange: boolean) => void;
  newChat: () => void;
  /** Close this chat into the archive. No confirmation: it is reversible,
   *  and the delete that is not lives on the archive view. */
  archiveThread: (id: string) => Promise<void>;
  saveRename: (title: string) => Promise<void>;
  renameBusy: boolean;
  /** A thread was just created by this chat: put it in the picker, record the
   *  selection, and carry the blank chat's outbox onto it. */
  adoptThread: (t: ThreadSummary | Thread) => void;
  /** The voice session started a thread of its own and this chat is on the
   *  blank slot. Same move, minus a summary to insert (the refresh brings it). */
  adoptThreadId: (id: string) => void;
  confirmNew: boolean;
  setConfirmNew: (v: boolean) => void;
  renameOpen: boolean;
  setRenameOpen: (v: boolean) => void;
};

export function useChatThread({
  scope, providersReady, variant, outboxRef,
  startNewRef, linkedThreadRef, initialThreadId,
  stripNewParam, setSelectedModel, setSelectedEffort, setSelectedPermissionMode,
  onError, onLeaveForList,
}: {
  scope: string;
  providersReady: boolean;
  variant: string;
  /** Durable storage for sends the server has not taken, pointed at the
   *  conversation being opened. Reached through a ref because it is built from
   *  the store, which is built from the thread this hook selects. */
  outboxRef: React.MutableRefObject<SendOutbox>;
  startNewRef: React.MutableRefObject<boolean>;
  linkedThreadRef: React.MutableRefObject<string | null>;
  initialThreadId?: string;
  stripNewParam: () => void;
  setSelectedModel: (id: string) => void;
  setSelectedEffort: (level: string) => void;
  setSelectedPermissionMode: (mode: string) => void;
  onError: (problem: ChatProblem | null) => void;
  /** The focused route has nothing left to show once its thread is deleted. */
  onLeaveForList: () => void;
}): ChatThread {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);
  const [cardStates, setCardStates] = useState<Record<string, CardState>>({});
  const [renameBusy, setRenameBusy] = useState(false);
  const [confirmNew, setConfirmNew] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  // Bumped by the Retry on the "couldn't open this conversation" block. The
  // resolver below is idempotent, so re-running it is what retrying means.
  const [threadLoadNonce, setThreadLoadNonce] = useState(0);

  // Live mirror so the loader can read the thread we are currently on WITHOUT
  // listing activeId in its deps (which would re-run it on every switch). It
  // uses this to keep an open chat put instead of snapping to the newest thread.
  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;

  // How this chat handles work: Direct (stays here, native subagents) or
  // Orchestrated (leaves as tickets). Server state, mirrored locally so the
  // control responds immediately. The seed matches chat_store.DEFAULT_CHAT_MODE;
  // a seed that disagrees makes the control flicker to the other mode on open,
  // which reads as the setting not having stuck.
  const [chatMode, setChatMode] = useState<ChatModeId>("direct");
  // A blank chat has no thread to write the mode to, so the choice is held and
  // applied at creation — the first message is the one that decides whether work
  // leaves the conversation, so the control cannot wait for it.
  const chatModeRef = useRef<ChatModeId>(chatMode);
  chatModeRef.current = chatMode;

  const refreshThreads = useCallback<() => Promise<ThreadSummary[]>>(async () => {
    // A backend restart briefly 502s every in-flight request while the edge has
    // no origin to reach. A restart is a pause, not a break, so the poll rides
    // out a transient failure quietly instead of throwing a banner that then
    // lingers after the operator has already moved to another chat (which is how
    // a general chat ended up showing a stale development-scoped error). Only a
    // persistent transient failure, or any non-transient error, surfaces.
    const backoff = [400, 1200, 2500];
    for (let attempt = 0; ; attempt++) {
      try {
        const list = await chatApi.listThreads(scope);
        setThreads(list);
        return list;
      } catch (e) {
        if (isTransientNetworkError(e) && attempt < backoff.length) {
          await new Promise((r) => setTimeout(r, backoff[attempt]));
          continue;
        }
        onError({
          text: `Couldn't refresh the list of conversations: ${e}. The list may be out of date.`,
          endpoint: threadListEndpoint(scope),
          // Reading the list again is the whole action, so the retry is the call
          // that just failed.
          retry: () => { void refreshThreads(); },
        });
        return [];
      }
    }
  }, [scope, onError]);

  // The namer runs detached and takes as long as it takes. This used to look
  // ONCE, 2.5 seconds later, and a namer slower than that left "New chat" in the
  // picker until the operator happened to reload — for a title that had been
  // sitting on the server the whole time. Look a few times, spaced out, and stop
  // as soon as the thread has a real title.
  const scheduleAutoNameRefetch = useCallback((wasFirstExchange: boolean) => {
    if (!wasFirstExchange) return;
    const id = activeIdRef.current;
    if (!id) return;
    // Backing off rather than polling flat: the common case is answered by the
    // first look, and the last is a long wait for a namer that is genuinely
    // slow. Each look stops the chain as soon as there is a title, so the usual
    // outcome is exactly the single request this used to make.
    const delays = [2500, 5000, 10000, 20000];
    let cancelled = false;
    for (const ms of delays) {
      window.setTimeout(async () => {
        // Cancelled by a look that already found the name, or the operator moved
        // to another conversation. Either way, stop asking.
        if (cancelled || activeIdRef.current !== id) return;
        const list = await refreshThreads();
        const named = list.find((t) => t.id === id)?.title?.trim();
        if (named) cancelled = true;
      }, ms);
    }
  }, [refreshThreads]);

  const chooseChatMode = useCallback((next: ChatModeId) => {
    const id = activeIdRef.current;
    const prev = chatMode;
    setChatMode(next);
    if (!id) return;   // no thread yet: applied when this chat is created
    void chatApi.setThreadMode(id, next).catch((e) => {
      // Put the control back rather than showing a mode the server does not
      // have: a chat that looks Direct while the server files tickets is worse
      // than a visible failure.
      setChatMode(prev);
      onError({
        text: `Couldn't switch chat mode: ${e}. The control is back on ${prev}.`,
        endpoint: `/api/chat/threads/${id}/mode`,
      });
    });
  }, [chatMode, onError]);

  // What a NEW chat starts in (Settings -> Chat). Applied whenever there is no
  // thread yet, so the blank chat's control shows the mode the next message will
  // actually run in rather than a hardcoded guess.
  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live && !activeIdRef.current) setChatMode(chatModeDef(p.default_mode ?? undefined).id); })
      .catch(() => { /* the platform default stands */ });
    return () => { live = false; };
  }, []);

  // The thread's own settings. NOT its messages.
  // Takes the thread DOCUMENT, not a list row. `card_states`, `effort` and
  // `permission_mode` are settings and only the document carries them; the
  // union this used to accept let a `ThreadSummary` in, where all three are
  // silently `undefined` and every picker fell back to its default.
  const adoptSettings = useCallback((t: Thread) => {
    outboxRef.current.bind(t.id);
    setCardStates(t.card_states ?? {});
    setChatMode(chatModeDef(t.mode).id);
    if (t.model) setSelectedModel(t.model);
    // "" when the thread has none: the meter falls back to showing the model's
    // default. Seeding this is what stops the picker and the server disagreeing
    // about the level after a reload.
    setSelectedEffort(t.effort ?? "");
    setSelectedPermissionMode(t.permission_mode ?? "");
  }, [outboxRef, setSelectedModel, setSelectedEffort, setSelectedPermissionMode]);

  useEffect(() => {
    // Gate: never load a thread under a scope that is still being resolved.
    if (!providersReady) return;
    let cancelled = false;
    (async () => {
      onError(null);
      setLoadingThreads(true);
      try {
        const list = await chatApi.listThreads(scope);
        if (cancelled) return;
        setThreads(list);
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
        if (cancelled) return;
        if (t) {
          setActiveId(t.id);
          writeSelection(scope, t.id);
          adoptSettings(t);
        } else {
          setActiveId(null);
          setCardStates({});
          // Landing blank: the outbox belongs to the blank-chat slot, and a
          // send that never reached the server is still owed delivery.
          outboxRef.current.bind(`scope:${scope}`);
        }
      } catch (e) {
        if (!cancelled) {
          onError({
            text: `Couldn't open this conversation: ${e}`,
            endpoint: threadListEndpoint(scope),
            // A real retry: re-runs the whole resolver (list, then select), not
            // just the list — refilling the list alone would leave the chat
            // sitting on no thread while claiming it had recovered.
            retry: () => setThreadLoadNonce((n) => n + 1),
          });
        }
      } finally {
        if (!cancelled) setLoadingThreads(false);
      }
    })();
    return () => { cancelled = true; };
    // scope and the linked thread drive the reload — the URL naming a different
    // conversation has to re-run this even when the route is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, initialThreadId, providersReady, threadLoadNonce]);

  const newChat = useCallback(() => {
    setActiveId(null);
    setCardStates({});
    // Move the outbox key to the blank-chat slot FIRST, or clearing it for the
    // new chat would wipe the sends still owed to the thread being left behind.
    outboxRef.current.resetToBlank();
    onError(null);
    setSelectedModel("");
    setSelectedEffort("");
    setSelectedPermissionMode("");
    // Persist the intent so a reload lands on a blank new chat rather than
    // silently re-adopting the most-recent thread.
    writeSelection(scope, NEW_CHAT);
  }, [outboxRef, scope, onError, setSelectedModel, setSelectedEffort, setSelectedPermissionMode]);

  const adoptThreadId = useCallback((id: string) => {
    setActiveId(id);
    // Persist the selection too. Adopting without it left a voice-started chat
    // unreachable on the next mount: the stored selection still said NEW_CHAT,
    // so the loader came back blank and the thread the operator had just been
    // talking to was only findable in the picker.
    writeSelection(scope, id);
    outboxRef.current.carryToThread(id);
  }, [scope, outboxRef]);

  const adoptThread = useCallback((t: ThreadSummary | Thread) => {
    // The caller has whichever shape it happens to hold: `createThread` answers
    // with the thread DOCUMENT, the picker holds list rows, and the two are not
    // the same type. The document has no `message_count` or `last_message`
    // because the list is what computes those, so a created thread used to go
    // into the picker with both `undefined` while the type said otherwise.
    // A thread this client just created has no messages yet, so the two values
    // are known here rather than needing a refetch.
    const row: ThreadSummary =
      "message_count" in t ? t : { ...t, message_count: 0, last_message: "" };
    // Put the new thread in the picker immediately. Without this a chat you had
    // just started was unreachable the moment you navigated away: it wasn't in
    // the local list, and nothing remembered you were in it.
    setThreads((list) => (list.some((x) => x.id === row.id) ? list : [row, ...list]));
    adoptThreadId(row.id);
  }, [adoptThreadId]);

  // ARCHIVING is what closing a chat from its own menu does. It is the same
  // motion the delete used to be (leave the conversation, take it out of the
  // list, go somewhere), minus the destruction: the thread is still there, in
  // the archive, where it can be restored or deleted deliberately.
  const archiveThread = useCallback(async (id: string) => {
    // AN ARCHIVE THAT FAILED MUST NOT LOOK LIKE ONE THAT WORKED. This used to be
    // a bare await: the dialog had already closed, so a 5xx skipped the refresh,
    // said nothing, and left the thread in the list looking gone with no hint
    // that anything had happened. Nothing after this line may run on a failure
    // either, or the chat navigates away from a conversation still on screen.
    try {
      await chatApi.archiveThread(id);
    } catch (e) {
      onError({
        text: `Couldn't archive this conversation: ${e}. It is still here; try again.`,
        endpoint: `/api/chat/threads/${id}/archive`,
      });
      return;
    }
    // The conversation is out of the lists, so what this browser was holding FOR
    // it goes too: an unsent queue against a chat you cannot reach is text
    // nothing will ever deliver. Restoring brings the thread back, not the
    // queue, which is the honest half of the round trip.
    forgetThread(id);
    // Archiving the conversation you are looking at has to take you somewhere.
    // The focused route is nothing BUT that conversation, so staying put left a
    // page addressing a chat that is no longer in any list.
    if (id === activeIdRef.current) {
      writeSelection(scope, NEW_CHAT);
      if (variant === "full") { onLeaveForList(); return; }
      newChat();
    }
    void refreshThreads();
  }, [newChat, refreshThreads, scope, variant, onLeaveForList, onError]);

  const saveRename = useCallback(async (title: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setRenameBusy(true);
    try {
      const t = await chatApi.renameThread(id, title);
      setThreads((list) => list.map((x) => (x.id === t.id ? { ...x, title: t.title } : x)));
      setRenameOpen(false);
    } catch (e) {
      onError({
        text: `Couldn't rename this conversation: ${e}. Its title is unchanged.`,
        endpoint: `/api/chat/threads/${id}`,
      });
    } finally {
      setRenameBusy(false);
    }
  }, [onError]);

  const activeThreadTitle = activeId ? threads.find((t) => t.id === activeId)?.title : undefined;

  return {
    threads, activeId, activeIdRef, loadingThreads, activeThreadTitle,
    chatMode, chatModeRef, chooseChatMode, cardStates, setCardStates,
    refreshThreads, scheduleAutoNameRefetch, newChat, archiveThread, saveRename, renameBusy,
    adoptThread, adoptThreadId,
    confirmNew, setConfirmNew, renameOpen, setRenameOpen,
  };
}
