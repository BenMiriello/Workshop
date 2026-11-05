// "Send message" as an action in the history (docs/dictation-and-clipboard.md,
// "Send and cancel"). To a chat that is not replying it goes at once, and Undo
// takes it back out (stopping its own reply if that is still running). To a
// chat mid-reply it is held for five seconds first, because once it joins a
// running reply it cannot come out without stopping that reply: Undo inside
// the hold just puts the words back, and after it Undo waits for the reply to
// end. Redo sends the same words again.

import { appendDraft } from "../../../lib/draftWire";
import {
  cancelSend, chatBusy, chatHref, draftOf, holdSend, inRunningReply, putBackHeld, sendChat, sendHeld,
  type CancelOutcome, type ChatTarget, type SendHandle,
} from "../../../lib/speech/chatTargets";
import { actionHistory, type Blocked, type Outcome } from "./actionHistory";
import { chatBecame, recordAction, short } from "./actionRun";
import type { CommandHost } from "./dictation";
import { chatPlace } from "./destination";
import { NOTICE_MS } from "./notices";
import type { Said } from "./takeBacks";

const chainOf = (t: ChatTarget) => (t.threadId ? `chat:${t.threadId}` : `draft:${t.draftKey}`);
const failure = (e: unknown) => (e instanceof Error ? e.message : String(e));

function outcomeText(o: CancelOutcome): string {
  switch (o.kind) {
    case "unsent": return "Not sent. Your words are back in the chat.";
    case "wentBack": return "Taken back. Your words are back in the chat.";
    case "notSent": return o.reason ? `Not sent: ${o.reason}` : "Not sent.";
    default: return o.reason ? `Could not take it back: ${o.reason}` : "Could not take it back.";
  }
}

export function send(host: CommandHost, chat: ChatTarget, at: Said | null): void {
  host.sentFrom(chatPlace(chat));
  void chatBusy(chat).then((busy) => sendAction(host, chat, at, busy));
}

function sendAction(host: CommandHost, chat: ChatTarget, at: Said | null, held: boolean): void {
  let target = chat;
  let words = "";
  let sent: Promise<SendHandle> | null = null;
  let hold: ReturnType<typeof holdSend> | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  // Joined a reply that was already running: no Undo until it ends.
  let joined = held;
  const name = short(chat.title || "chat");

  // A new chat's first send makes its conversation; the destination and the
  // entry follow it.
  const moved = (now: ChatTarget) => {
    const into = host.getState().into;
    if (into?.kind === "chat" && into.chat.draftKey === target.draftKey && now.draftKey !== target.draftKey) {
      host.setDestination(chatPlace(now));
    }
    chatBecame(target, now);
    target = now;
    actionHistory.update(e.id, { href: chatHref(now), chain: chainOf(now) });
  };

  const go = (h: Promise<SendHandle>): Promise<Outcome> => {
    sent = h;
    return h.then((x) => { words = x.text; return x.result; }).then((r) => {
      if (r.ok) { moved(r.target); return { ok: true, text: `Sent to ${name}`, href: chatHref(r.target) }; }
      return { ok: false, text: `Not sent: ${r.error}`, href: chatHref(target) };
    }, (err) => ({ ok: false, text: `Not sent: ${failure(err)}`, href: chatHref(target) }));
  };

  const report = (p: Promise<Outcome>) => void p.then((o) => { if (!o.ok) host.notice({ text: o.text, href: o.href }); });

  const undo = async (): Promise<Outcome> => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
      const h = await hold!;
      await putBackHeld(h);
      return { ok: true, text: "Not sent. Your words are back in the chat.", href: chatHref(target) };
    }
    if (!sent) return { ok: false, text: "Nothing was sent." };
    const o = await cancelSend(await sent);
    if (o.target.draftKey !== target.draftKey) moved(o.target);
    const ok = o.kind !== "refused";
    if (ok) joined = false;
    return { ok, text: outcomeText(o), href: o.href };
  };

  const redo = async (): Promise<Outcome> => {
    const now = (await draftOf(target)).trim();
    if (!now && words) await appendDraft(target.draftKey, words);
    return go(sendChat(target));
  };

  const check = async (done: boolean): Promise<Blocked> => {
    if (done) return joined && sent && await inRunningReply(await sent) ? { undo: "The reply is still running" } : {};
    if (await chatBusy(target)) return { redo: "The chat is replying" };
    const now = (await draftOf(target)).trim();
    return now && now !== words ? { redo: "The chat's box has other words" } : {};
  };

  const e = recordAction(host, {
    label: `${held ? "Sending" : "Sent"} to ${name}`, href: chatHref(chat), chain: chainOf(chat), undo, redo, check,
  }, at);

  if (!held) { report(go(sendChat(chat))); return; }
  hold = holdSend(chat);
  void hold.then((h) => { words = h.text; });
  timer = setTimeout(() => {
    timer = null;
    actionHistory.update(e.id, { label: `Sent to ${name}` });
    report(go(hold!.then(sendHeld)));
  }, NOTICE_MS);
}
