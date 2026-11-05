// The Actions other than Send, each applied at once and taken into the history
// with its Undo and Redo (actionHistory.ts). An action that moved the operator
// (a view switched to a new chat, the side panel opened) puts them back on
// Undo.

import { chatApi } from "../../../lib/chat";
import { modelLabel } from "../../chat/models";
import {
  chatHref, chatModels, modelNow, openChat, panelNow, restoreModel, restorePanel, setChatModel, startNewChat, switchChat,
  type ChatTarget,
} from "../../../lib/speech/chatTargets";
import type { Outcome } from "./actionHistory";
import { onChatBecame, recordAction, short } from "./actionRun";
import type { CommandHost } from "./dictation";
import { chatPlace } from "./destination";
import { phraseHistory } from "./phraseHistory";
import { numbered, numberGroups } from "./numbered";
import type { Said } from "./takeBacks";

const done = (text: string, href?: string | null): Outcome => ({ ok: true, text, href });

/** A new chat in `from`'s scope: where words go, and what a view showing
 *  `from` shows. Undo goes back to `from`. */
export function newChat(host: CommandHost, from: ChatTarget, at: Said | null): void {
  const before = host.getState().into;
  let made = startNewChat(from.scope, from);
  host.setDestination(chatPlace(made));
  onChatBecame((was, now) => { if (was.draftKey === made.draftKey) made = now; });
  const intoMade = () => {
    const into = host.getState().into;
    return into?.kind === "chat" && into.chat.draftKey === made.draftKey;
  };
  recordAction(host, {
    label: "New chat",
    undo: async () => {
      switchChat(made, from);
      if (intoMade()) host.setDestination(before ?? chatPlace(from));
      return done(`Back to ${short(from.title || "chat")}`, chatHref(from));
    },
    redo: async () => {
      if (made.threadId) switchChat(from, made);
      else made = startNewChat(from.scope, from);
      host.setDestination(chatPlace(made));
      return done(made.threadId ? `Back to ${short(made.title || "chat")}` : "New chat");
    },
  }, at);
}

/** Set the chat's model to the one whose name is most like `query`. False
 *  when none is. */
export async function chatModel(host: CommandHost, chat: ChatTarget, query: string, at: Said | null): Promise<boolean> {
  const pick = await setChatModel(chat, query).catch(() => undefined);
  if (pick === undefined) { host.notice({ text: "Could not change the model." }); return false; }
  if (!pick) { host.notice({ text: `No model like "${short(query)}"` }); return false; }
  const was = pick.previous ? modelLabel(pick.previous) : "the default";
  recordAction(host, {
    label: `Model: ${pick.label}`,
    href: chatHref(chat),
    undo: async () => { await restoreModel(chat, pick.previous); return done(`Model back to ${was}`); },
    redo: async () => { await restoreModel(chat, pick.id); return done(`Model: ${pick.label}`); },
    check: async (standing) => (
      standing && await modelNow(chat) !== pick.id ? { undo: "The model was changed since" } : {}
    ),
  }, at);
  return true;
}

/** Show the chat in the side panel. Undo puts back what the panel showed. */
export function openChatAction(host: CommandHost, chat: ChatTarget, at: Said | null): void {
  const was = panelNow();
  openChat(chat);
  const name = short(chat.title || "chat");
  recordAction(host, {
    label: `Opened ${name}`,
    undo: async () => { restorePanel(was); return done(was.open ? "Side panel put back" : "Side panel closed"); },
    redo: async () => { openChat(chat); return done(`Opened ${name}`); },
  }, at);
}

/** "Scratch that": take out the last phrase dictated. */
export function scratch(host: CommandHost, at: Said | null): void {
  const e = host.undo(true);
  if (!e) { host.notice({ text: "Nothing to remove" }); return; }
  const changed = "That text changed since";
  recordAction(host, {
    label: `Removed "${short(e.text)}"`,
    undo: async () => (host.putBack(e) ? done(`Put back "${short(e.text)}"`) : { ok: false, text: changed }),
    redo: async () => (host.takeOut(e) ? done(`Removed "${short(e.text)}"`) : { ok: false, text: changed }),
    check: async (standing) => {
      const now = phraseHistory.standing(e);
      if (standing) return now === "out" ? {} : { undo: changed };
      return now === "in" ? {} : { redo: changed };
    },
  }, at);
}

/** The chat's models as numbered choices; picking one sets it as "chat model"
 *  does. */
export async function chooseModel(host: CommandHost, chat: ChatTarget): Promise<void> {
  const list = await chatModels(chat).catch(() => []);
  if (!list.length) { host.notice({ text: "No models to choose from" }); return; }
  numbered.show({
    kind: "choices",
    title: "Change the model",
    groups: numberGroups([{ label: short(chat.title || "This chat"), items: list.map((m) => ({
      label: m.label, run: () => void chatModel(host, chat, m.label, null),
    })) }]),
  });
}

/** "Stop the reply": end the reply the chat is writing, as its Stop button
 *  does. Not in the history: a stopped reply cannot be resumed. */
export async function stopReply(host: CommandHost, chat: ChatTarget): Promise<void> {
  const page = chat.threadId ? await chatApi.threadEvents(chat.threadId, 0, { tail: 1 }).catch(() => null) : null;
  const turn = page?.active_turn?.turn_id;
  if (!turn) { host.notice({ text: "No reply running" }); return; }
  await chatApi.stopTurn(turn).catch(() => null);
  host.notice({ text: "Reply stopped" });
}
