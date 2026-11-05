// Taking an action into the history and undoing or redoing it, with what the
// dictation bar says about each: the action for five seconds with Cancel (its
// Undo), then how an undo or redo went.

import type { ChatTarget } from "../../../lib/speech/chatTargets";
import { actionHistory, type ActionEntry, type ActionSpec } from "./actionHistory";
import type { NoticeInput } from "./notices";
import type { CommandHost } from "./dictation";
import type { Said } from "./takeBacks";
import type { Run } from "./voiceActionTypes";

export type NoticeHost = { notice: (n: NoticeInput | null) => void };

const SHORT = 24;

export function short(text: string): string {
  const t = text.trim();
  return t.length > SHORT ? `${t.slice(0, SHORT - 1).trimEnd()}…` : t;
}

/** A name was expected and none fits: words said while writing were words
 *  after all; with only commands heard, or worded freely, the bar says so. */
export function notCommand(host: CommandHost, r: Run, said: string): void {
  if (host.getState().mode === "commands" || r.loose) host.notice({ text: said });
  else host.write(r.words);
}

export function recordAction(host: NoticeHost, spec: ActionSpec, said: Said | null): ActionEntry {
  const e = actionHistory.record(spec, said);
  host.notice({ text: spec.label, href: spec.href, cancel: () => void undoAction(host, e.id) });
  return e;
}

export async function undoAction(host: NoticeHost, id: number): Promise<void> {
  host.notice({ text: "Undoing", until: null });
  const o = await actionHistory.undo(id);
  host.notice({ text: o.text, href: o.href });
}

export async function redoAction(host: NoticeHost, id: number): Promise<void> {
  host.notice({ text: "Redoing", until: null });
  const o = await actionHistory.redo(id);
  host.notice({ text: o.text, href: o.href });
}

// A new chat becomes a conversation on its first send, and a chat whose first
// message is taken back goes back to being a new chat. An action about that
// chat (New chat) follows it here.
type Became = (was: ChatTarget, now: ChatTarget) => void;
const watchers = new Set<Became>();

export function onChatBecame(fn: Became): void {
  watchers.add(fn);
}

export function chatBecame(was: ChatTarget, now: ChatTarget): void {
  if (was.draftKey !== now.draftKey) watchers.forEach((fn) => fn(was, now));
}
