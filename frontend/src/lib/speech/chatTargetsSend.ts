// "Send message" and the "cancel" that takes it back. The cases and their
// order are "Send and cancel" in docs/dictation-and-clipboard.md; the decision
// is chatTargetsPlan.cancelStep, and this file carries it out.

import { HttpError } from "@workshop/ui";
import { chatApi, newClientId, shortHttpError } from "../chat";
import { writeSelection } from "../chatSelection";
import {
  draftAppendsSettled, readDraftNewest, writeDraftOutside,
  type DraftAttachment, type DraftValue,
} from "../draftWire";
import { SEND_TIMEOUT_MS, withDeadline } from "../requestTimeout";
import type { ChatModeId } from "../../components/chat/chatModes";
import type { OpenChat } from "../../components/chat/openChats";
import {
  chatHref, findOpen, newChatTarget, viewsOf, withThread, type ChatTarget,
} from "./chatTargetsCore";
import { cancelStep, readChatState, type CancelStep } from "./chatTargetsPlan";
import { restoreAttachments, restoreText } from "./chatTargetsText";

export type SendResult = { ok: true; target: ChatTarget } | { ok: false; error: string };

export type SendHandle = {
  /** The chat as it was when sent. A new chat's conversation is on `result`. */
  target: ChatTarget;
  clientId: string | null;
  /** What went out of the draft, to put back on a cancel. */
  text: string;
  attachments: DraftAttachment[];
  via: "composer" | "direct";
  at: number;
  /** Settles when the send request has answered. */
  result: Promise<SendResult>;
};

export type CancelOutcome = {
  kind: "unsent" | "wentBack" | "notSent" | "refused";
  reason?: string;
  href: string;
  /** The chat afterwards: a new chat's send created its conversation. */
  target: ChatTarget;
};

const QUIET_MS = 10_000;
const POLL_MS = 400;
const LOG_TAIL = 100;

const sleep = (ms: number) => new Promise<void>((r) => { setTimeout(r, ms); });
// A refusal's sentence without the status code the HTTP layer puts first.
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d{3}:?\s+/, "");

/** Send what the chat's send button would send: through its composer when
 *  one is on screen (its model, files and queue rules apply), else the draft
 *  straight to the server. Words appended just before are included. */
export async function sendChat(t: ChatTarget): Promise<SendHandle> {
  await draftAppendsSettled(t.draftKey);
  const open = findOpen(t);
  return open ? sendThroughComposer(t, open) : sendDirect(t);
}

/** Lets words written into the box a moment ago reach the composer's state. */
export async function settleComposer(open: OpenChat) {
  for (let i = 0; i < 10; i++) {
    await sleep(30);
    const el = open.composer.input();
    if (!el || el.value === open.composer.getDraft().text) return;
  }
}

async function sendThroughComposer(t: ChatTarget, open: OpenChat): Promise<SendHandle> {
  await settleComposer(open);
  const { text, attachments } = open.composer.getDraft();
  const sent = open.composer.send();
  const base = { target: t, text: text.trim(), attachments, via: "composer" as const, at: Date.now() };
  if (!sent) {
    const error = text.trim() || attachments.length ? "The chat cannot send yet." : "Nothing to send.";
    return { ...base, clientId: null, result: Promise.resolve({ ok: false, error }) };
  }
  const result = sent.done.then((o): SendResult => (o.ok
    ? { ok: true, target: withThread({ ...t, scope: open.scope }, open.composer.threadId()) }
    : { ok: false, error: o.error }));
  return { ...base, clientId: sent.clientId, result };
}

async function sendDirect(t: ChatTarget): Promise<SendHandle> {
  const d = await readDraftNewest(t.draftKey);
  const text = d.text.trim();
  const base = { target: t, text, attachments: d.attachments, via: "direct" as const, at: Date.now() };
  if (!text && !d.attachments.length) {
    return { ...base, clientId: null, result: Promise.resolve({ ok: false, error: "Nothing to send." }) };
  }
  const clientId = newClientId();
  return { ...base, clientId, result: postTurn(t, d, text, clientId) };
}

export async function postTurn(
  t: ChatTarget, d: DraftValue, text: string, clientId: string, clearDraft = true,
): Promise<SendResult> {
  try {
    let threadId = t.threadId;
    if (!threadId) {
      const created = await chatApi.createThread(t.scope, "", d.settings.mode as ChatModeId | undefined);
      threadId = created.id;
      // The scope opens on the chat just started, as it does after a composer's send.
      writeSelection(t.scope, threadId);
    }
    // A new chat's picks travel with its first message; a chat that exists
    // keeps its own.
    const picks = t.threadId ? {} : {
      ...(d.settings.model ? { model: d.settings.model } : {}),
      ...(d.settings.effort ? { effort: d.settings.effort } : {}),
      ...(d.settings.permission_mode ? { permission_mode: d.settings.permission_mode } : {}),
    };
    const id = threadId;
    const r = await withDeadline((signal) => fetch(`/api/chat/threads/${encodeURIComponent(id)}/turns`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text, client_id: clientId, draft_key: t.draftKey, sent_at: new Date().toISOString(),
        ...picks, ...(d.attachments.length ? { attachments: d.attachments } : {}),
      }),
    }), SEND_TIMEOUT_MS);
    if (!r.ok) throw new HttpError(r.status, shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
    const res = await r.json() as { deferred?: boolean };
    if (res.deferred) return { ok: false, error: "The chat is busy with a command this server cannot queue." };
    if (clearDraft) writeDraftOutside(t.draftKey, { text: "", attachments: [], settings: {} });
    return { ok: true, target: withThread(t, id) };
  } catch (e) {
    return { ok: false, error: errorText(e) };
  }
}

async function readStep(threadId: string, clientId: string): Promise<CancelStep> {
  const page = await chatApi.threadEvents(threadId, 0, { tail: LOG_TAIL });
  return cancelStep({ answered: true, sendOk: true, ...readChatState(page, clientId) });
}

/** After a stop: wait for the chat to go quiet, then go back. */
async function untilQuiet(threadId: string, clientId: string): Promise<CancelStep> {
  const end = Date.now() + QUIET_MS;
  while (Date.now() < end) {
    await sleep(POLL_MS);
    const step = await readStep(threadId, clientId).catch(() => null);
    if (step?.kind === "goBack" || step?.kind === "refuse" && step.reason === "Another turn started.") return step;
  }
  return { kind: "refuse", reason: "The chat did not go quiet." };
}

/** Words (and, with its composer open, files) back into the chat's draft:
 *  the sent text, then a line break and anything typed since. */
async function putBack(h: SendHandle, t: ChatTarget) {
  const open = findOpen(t);
  if (open) {
    const cur = open.composer.getDraft();
    open.composer.setDraft(restoreAttachments(h.attachments, cur.attachments), restoreText(h.text, cur.text));
    return;
  }
  const cur = await readDraftNewest(t.draftKey);
  writeDraftOutside(t.draftKey, { ...cur, text: restoreText(h.text, cur.text) });
}

/** The chat's first message is being taken back, so the chat goes: its turn
 *  stopped, the chat archived (reversible), the words back in the scope's new
 *  chat with anything typed since, and every view of it on that new chat. */
async function unsendChat(h: SendHandle, t: ChatTarget, turnId: string | null): Promise<CancelOutcome> {
  const threadId = t.threadId as string;
  if (turnId) {
    await chatApi.stopTurn(turnId).catch(() => null);
    const end = Date.now() + QUIET_MS;
    while (Date.now() < end) {
      await sleep(POLL_MS);
      const page = await chatApi.threadEvents(threadId, 0, { tail: 1 }).catch(() => null);
      if (page && !page.active_turn && !page.pending_operation) break;
    }
  }
  await chatApi.archiveThread(threadId);
  const fresh = newChatTarget(t.scope);
  const open = findOpen(t);
  const since = open ? open.composer.getDraft() : await readDraftNewest(t.draftKey);
  const cur = await readDraftNewest(fresh.draftKey);
  // A new-chat draft already holding words keeps them, ahead of the message.
  writeDraftOutside(fresh.draftKey, {
    text: restoreText(restoreText(cur.text.trimEnd(), h.text), since.text),
    attachments: restoreAttachments(cur.attachments, restoreAttachments(h.attachments, since.attachments)),
    settings: cur.settings,
  });
  viewsOf(t).forEach((c) => c.composer.startNew());
  return { kind: "unsent", href: chatHref(fresh), target: fresh };
}

/** Take back a message sent within the last 5 s. A cancel asked for while the
 *  send is still in flight is carried out the moment it answers. */
export async function cancelSend(h: SendHandle): Promise<CancelOutcome> {
  const r = await h.result;
  if (!r.ok || !h.clientId) {
    // A composer has already emptied its box and holds the message as a
    // failed send, which would go out on its own after a dropped connection.
    // Nothing was sent, so the words go back where they were.
    const open = h.via === "composer" && h.clientId ? findOpen(h.target) : null;
    if (open && h.clientId) {
      open.composer.dropSend(h.clientId);
      await putBack(h, h.target);
    }
    return { kind: "notSent", reason: r.ok ? undefined : r.error, href: chatHref(h.target), target: h.target };
  }
  const t = r.target;
  const href = chatHref(t);
  const threadId = t.threadId as string;
  const refused = (reason: string): CancelOutcome => ({ kind: "refused", reason, href, target: t });
  try {
    let step = await readStep(threadId, h.clientId);
    if (step.kind === "unqueue") {
      const gone = await chatApi.cancelQueued(threadId, h.clientId).then(() => true, () => false);
      if (gone) {
        findOpen(t)?.composer.resync();
        await putBack(h, t);
        return { kind: "unsent", href, target: t };
      }
      // It left the queue in the meantime: read again and go on from there.
      step = await readStep(threadId, h.clientId);
    }
    if (step.kind === "unsendChat") return await unsendChat(h, t, step.turnId);
    let stopped = false;
    if (step.kind === "stop") {
      await chatApi.stopTurn(step.turnId).catch(() => null);
      stopped = true;
      step = await untilQuiet(threadId, h.clientId);
    }
    if (step.kind !== "goBack") {
      // Stopping holds the chat's queue; this stop was the cancel's, not the
      // operator's, so a refusal must not leave typed-ahead messages stuck.
      if (stopped) await chatApi.releaseQueue(threadId).catch(() => null);
      return refused(step.kind === "refuse" ? step.reason : "The message could not be taken back.");
    }
    await chatApi.unsendMessage(threadId, step.anchor);
    findOpen(t)?.composer.resync();
    await putBack(h, t);
    return { kind: "wentBack", href, target: t };
  } catch (e) {
    return refused(errorText(e));
  }
}
