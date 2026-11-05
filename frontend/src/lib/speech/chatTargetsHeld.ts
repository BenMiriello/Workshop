// A send held back for its cancel window, used when the chat is mid-reply: a
// message that joined a running reply could not be taken back without stopping
// that reply, so it waits out the window first, out of the box.

import { chatApi, newClientId } from "../chat";
import { draftAppendsSettled, readDraftNewest, writeDraftOutside, type DraftAttachment } from "../draftWire";
import { findOpen, type ChatTarget } from "./chatTargetsCore";
import { postTurn, settleComposer, type SendHandle } from "./chatTargetsSend";
import { readChatState } from "./chatTargetsPlan";
import { restoreAttachments, restoreText } from "./chatTargetsText";

/** The words are out of the chat's box (so what is said next starts a new
 *  message) and in here. */
export type Held = { target: ChatTarget; text: string; attachments: DraftAttachment[] };

/** Take what the send button would send out of the chat's box now, to send
 *  when the hold ends. Words appended just before are included. */
export async function holdSend(t: ChatTarget): Promise<Held> {
  await draftAppendsSettled(t.draftKey);
  const open = findOpen(t);
  if (open) {
    await settleComposer(open);
    const d = open.composer.getDraft();
    open.composer.setDraft([], "");
    return { target: t, text: d.text.trim(), attachments: d.attachments };
  }
  const d = await readDraftNewest(t.draftKey);
  writeDraftOutside(t.draftKey, { ...d, text: "", attachments: [] });
  return { target: t, text: d.text.trim(), attachments: d.attachments };
}

/** Send a held message. Whatever was said into the box since stays there. */
export async function sendHeld(h: Held): Promise<SendHandle> {
  const base = { target: h.target, text: h.text, attachments: h.attachments, via: "direct" as const, at: Date.now() };
  if (!h.text && !h.attachments.length) {
    return { ...base, clientId: null, result: Promise.resolve({ ok: false, error: "Nothing to send." }) };
  }
  const d = await readDraftNewest(h.target.draftKey);
  const clientId = newClientId();
  return { ...base, clientId, result: postTurn(h.target, { ...d, attachments: h.attachments }, h.text, clientId, false) };
}

/** A held message that is not going: its words back in front of the box's. */
export async function putBackHeld(h: Held): Promise<void> {
  const open = findOpen(h.target);
  if (open) {
    const cur = open.composer.getDraft();
    open.composer.setDraft(restoreAttachments(h.attachments, cur.attachments), restoreText(h.text, cur.text));
    return;
  }
  const cur = await readDraftNewest(h.target.draftKey);
  writeDraftOutside(h.target.draftKey, {
    ...cur,
    text: restoreText(h.text, cur.text),
    attachments: restoreAttachments(h.attachments, cur.attachments),
  });
}

/** The words in the chat's box now. */
export async function draftOf(t: ChatTarget): Promise<string> {
  const open = findOpen(t);
  return open ? open.composer.getDraft().text : (await readDraftNewest(t.draftKey)).text;
}

/** Whether the chat is in the middle of a reply (or a /compact), so a send
 *  would join it rather than start one. */
export async function chatBusy(t: ChatTarget): Promise<boolean> {
  if (!t.threadId) return false;
  const page = await chatApi.threadEvents(t.threadId, 0, { tail: 1 }).catch(() => null);
  return !!(page?.active_turn || page?.pending_operation);
}

/** A sent message is part of a reply still running: taking it back would
 *  stop that reply. False while it waits on the queue, or once the reply ends. */
export async function inRunningReply(h: SendHandle): Promise<boolean> {
  const r = await h.result;
  if (!r.ok || !h.clientId || !r.target.threadId) return false;
  const page = await chatApi.threadEvents(r.target.threadId, 0, { tail: 100 }).catch(() => null);
  if (!page) return false;
  const s = readChatState(page, h.clientId);
  return !!s.activeTurn && !s.queued && !!s.message;
}
