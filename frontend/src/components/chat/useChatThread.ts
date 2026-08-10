import { useCallback, useEffect, useRef, useState } from "react";
import { chatApi, type CardState, type Thread, type ThreadSummary } from "../../lib/chat";
import {
  NEW_CHAT, readSelection, writeQueue, writeSelection,
} from "../../lib/chatSelection";
import type { ChatModeId } from "./chatModes";
import type { MessageQueue } from "./useMessageQueue";

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
  refreshThreads: () => Promise<void>;
  scheduleAutoNameRefetch: (wasFirstExchange: boolean) => void;
  newChat: () => void;
  removeThread: (id: string) => Promise<void>;
  saveRename: (title: string) => Promise<void>;
  renameBusy: boolean;
  /** A thread was just created by this chat: put it in the picker, record the
   *  selection, and carry the blank-chat queue onto it. */
  adoptThread: (t: ThreadSummary) => void;
  /** The voice session started a thread of its own and this chat is on the
   *  blank slot. Same move, minus a summary to insert (the refresh brings it). */
  adoptThreadId: (id: string) => void;
  confirmDeleteId: string | null;
  setConfirmDeleteId: (id: string | null) => void;
  confirmNew: boolean;
  setConfirmNew: (v: boolean) => void;
  renameOpen: boolean;
  setRenameOpen: (v: boolean) => void;
};

export function useChatThread({
  scope, providersReady, variant, queue,
  startNewRef, linkedThreadRef, initialThreadId,
  stripNewParam, setSelectedModel, setSelectedEffort, onError, onLeaveForList,
}: {
  scope: string;
  providersReady: boolean;
  variant: string;
  queue: MessageQueue;
  startNewRef: React.MutableRefObject<boolean>;
  linkedThreadRef: React.MutableRefObject<string | null>;
  initialThreadId?: string;
  stripNewParam: () => void;
  setSelectedModel: (id: string) => void;
  setSelectedEffort: (level: string) => void;
  onError: (msg: string | null) => void;
  /** The focused route has nothing left to show once its thread is deleted. */
  onLeaveForList: () => void;
}): ChatThread {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loadingThreads, setLoadingThreads] = useState(true);
  const [cardStates, setCardStates] = useState<Record<string, CardState>>({});
  const [renameBusy, setRenameBusy] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmNew, setConfirmNew] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);

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

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await chatApi.listThreads(scope));
    } catch (e) {
      onError(String(e));
    }
  }, [scope, onError]);

  const scheduleAutoNameRefetch = useCallback((wasFirstExchange: boolean) => {
    if (!wasFirstExchange) return;
    window.setTimeout(() => { void refreshThreads(); }, 2500);
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
      onError(`Couldn't switch chat mode: ${e}`);
    });
  }, [chatMode, onError]);

  // What a NEW chat starts in (Settings -> Chat). Applied whenever there is no
  // thread yet, so the blank chat's control shows the mode the next message will
  // actually run in rather than a hardcoded guess.
  useEffect(() => {
    let live = true;
    void chatApi.getChatPrefs()
      .then((p) => { if (live && !activeIdRef.current) setChatMode(p.default_mode); })
      .catch(() => { /* the platform default stands */ });
    return () => { live = false; };
  }, []);

  // The thread's own settings. NOT its messages.
  const adoptSettings = useCallback((t: Thread | ThreadSummary) => {
    queue.bindThread(t.id);
    setCardStates(t.card_states ?? {});
    setChatMode(t.mode === "orchestrated" ? "orchestrated" : "direct");
    if (t.model) setSelectedModel(t.model);
    // "" when the thread has none: the meter falls back to showing the model's
    // default. Seeding this is what stops the picker and the server disagreeing
    // about the level after a reload.
    setSelectedEffort(t.effort ?? "");
  }, [queue, setSelectedModel, setSelectedEffort]);

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
          // Landing blank: the queue belongs to the blank-chat slot, and
          // anything already waiting there is still owed delivery.
          queue.bindBlank();
        }
      } catch (e) {
        if (!cancelled) onError(String(e));
      } finally {
        if (!cancelled) setLoadingThreads(false);
      }
    })();
    return () => { cancelled = true; };
    // scope and the linked thread drive the reload — the URL naming a different
    // conversation has to re-run this even when the route is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, initialThreadId, providersReady]);

  const newChat = useCallback(() => {
    setActiveId(null);
    setCardStates({});
    // Move the queue key to the blank-chat slot FIRST, or clearing the composer
    // queue for the new chat would wipe the queue still owed to the thread being
    // left behind.
    queue.resetToBlank();
    onError(null);
    setSelectedModel("");
    setSelectedEffort("");
    // Persist the intent so a reload lands on a blank new chat rather than
    // silently re-adopting the most-recent thread.
    writeSelection(scope, NEW_CHAT);
  }, [queue, scope, onError, setSelectedModel, setSelectedEffort]);

  const adoptThreadId = useCallback((id: string) => {
    setActiveId(id);
    // Persist the selection too. Adopting without it left a voice-started chat
    // unreachable on the next mount: the stored selection still said NEW_CHAT,
    // so the loader came back blank and the thread the operator had just been
    // talking to was only findable in the picker.
    writeSelection(scope, id);
    queue.carryToThread(id);
  }, [scope, queue]);

  const adoptThread = useCallback((t: ThreadSummary) => {
    // Put the new thread in the picker immediately. Without this a chat you had
    // just started was unreachable the moment you navigated away: it wasn't in
    // the local list, and nothing remembered you were in it.
    setThreads((list) => (list.some((x) => x.id === t.id) ? list : [t, ...list]));
    adoptThreadId(t.id);
  }, [adoptThreadId]);

  const removeThread = useCallback(async (id: string) => {
    await chatApi.deleteThread(id);
    // Deleting the conversation you are looking at has to take you somewhere.
    // The focused route is nothing BUT that conversation, so staying put left a
    // page addressing a thread the server no longer has.
    if (id === activeIdRef.current) {
      writeSelection(scope, NEW_CHAT);
      writeQueue(id, []);
      if (variant === "full") { onLeaveForList(); return; }
      newChat();
    }
    void refreshThreads();
  }, [newChat, refreshThreads, scope, variant, onLeaveForList]);

  const saveRename = useCallback(async (title: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setRenameBusy(true);
    try {
      const t = await chatApi.renameThread(id, title);
      setThreads((list) => list.map((x) => (x.id === t.id ? { ...x, title: t.title } : x)));
      setRenameOpen(false);
    } catch (e) {
      onError(String(e));
    } finally {
      setRenameBusy(false);
    }
  }, [onError]);

  const activeThreadTitle = activeId ? threads.find((t) => t.id === activeId)?.title : undefined;

  return {
    threads, activeId, activeIdRef, loadingThreads, activeThreadTitle,
    chatMode, chatModeRef, chooseChatMode, cardStates, setCardStates,
    refreshThreads, scheduleAutoNameRefetch, newChat, removeThread, saveRename, renameBusy,
    adoptThread, adoptThreadId,
    confirmDeleteId, setConfirmDeleteId, confirmNew, setConfirmNew, renameOpen, setRenameOpen,
  };
}
