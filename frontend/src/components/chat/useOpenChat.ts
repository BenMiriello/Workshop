// Registers one mounted chat in openChats, with a composer handle that reads
// everything through refs, so the handle's identity holds for the life of the
// mount while the draft, the thread and the model change under it.

import { useEffect, useMemo, useRef, useState } from "react";
import type { DraftAttachment } from "../../lib/draftWire";
import { openChatStore, type ChatWhere, type ComposerSend, type OpenComposer } from "./openChats";

export type OpenChatLive = {
  where: ChatWhere;
  contextKey: string;
  scope: string;
  activeIdRef: React.MutableRefObject<string | null>;
  activeId: string | null;
  draftKey: string;
  title: string;
  draft: string;
  attachments: DraftAttachment[];
  setDraftText: (text: string) => void;
  setDraftAttachments: (a: DraftAttachment[]) => void;
  send: () => ComposerSend | null | undefined;
  dropSend: (clientId: string) => void;
  resync: () => void;
  model: string;
  modelOptions: string[];
  setModel: (id: string) => void;
  startNew: () => void;
  openThread: (threadId: string, contextKey: string) => void;
  inputRef: React.RefObject<HTMLTextAreaElement>;
};

export function useOpenChat(live: OpenChatLive): void {
  const ref = useRef(live);
  ref.current = live;
  const [mountId] = useState(() => openChatStore.mountId());

  const composer = useMemo<OpenComposer>(() => ({
    send: () => ref.current.send() ?? null,
    getDraft: () => ({ text: ref.current.draft, attachments: ref.current.attachments }),
    setDraft: (attachments, text) => {
      ref.current.setDraftText(text);
      ref.current.setDraftAttachments(attachments);
    },
    dropSend: (clientId) => ref.current.dropSend(clientId),
    resync: () => ref.current.resync(),
    threadId: () => ref.current.activeIdRef.current,
    model: () => ref.current.model,
    modelOptions: () => ref.current.modelOptions,
    setModel: (id) => ref.current.setModel(id),
    startNew: () => ref.current.startNew(),
    openThread: (threadId, contextKey) => ref.current.openThread(threadId, contextKey),
    input: () => ref.current.inputRef.current,
  }), []);

  const { where, contextKey, scope, activeId, draftKey, title } = live;
  useEffect(() => {
    openChatStore.put({
      mountId, where, contextKey, scope, threadId: activeId, draftKey, title, composer,
    });
  }, [mountId, where, contextKey, scope, activeId, draftKey, title, composer]);
  useEffect(() => () => openChatStore.remove(mountId), [mountId]);
}
