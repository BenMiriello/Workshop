// Everything dictation can do by voice, as one list (docs/dictation-and-
// clipboard.md, "Spoken commands"): what each is called, the group Commands
// lists it under, when it makes sense, and what it does. The server knows
// the phrases (services/speech/command_list.py) and sends the id; a sentence
// worded freely comes as guesses (guessRule.ts) and runs the same entry with
// `loose`, its arguments found inside the sentence. Mode switches said aloud
// are carried out by the server; listed here so they can be picked too.

import { openChats } from "../../../lib/speech/chatTargets";
import { actionHistory } from "./actionHistory";
import { notCommand, undoAction } from "./actionRun";
import { chatModel, chooseModel, newChat, openChatAction, scratch, stopReply } from "./chatActions";
import { showCommands } from "./commandsList";
import type { CommandHost } from "./dictation";
import { pageLabels } from "./labels/pageLabels";
import { numbered, numbersOn } from "./numbered";
import { choosePlace, openPlace, placeNamed } from "./openAction";
import { clickTarget, goBack, goForward, pickNumber, scrollPage } from "./pageActions";
import { chooseField, closeTop, scrollEdge, stepTab, writeInto } from "./pageMoves";
import { readLastReply, readPage, readSpeed, stopReading } from "./readActions";
import { reader } from "./reader";
import { send } from "./sendAction";
import type { ActionCtx, VoiceAction } from "./voiceActionTypes";

export type { ActionCtx, Run, VoiceAction } from "./voiceActionTypes";

export function ctxOf(host: CommandHost): ActionCtx {
  const into = host.getState().into;
  const chat = into?.kind === "chat" ? into.chat : null;
  return { host, chat, anyChat: chat ?? openChats()[0] ?? null };
}

const mode = (m: "dictation" | "mixed" | "commands") => (c: ActionCtx) => c.host.getState().mode !== m;

export const VOICE_ACTIONS: VoiceAction[] = [
  { id: "scroll_down", label: "Scroll down", group: "This page", run: (c) => scrollPage(c.host, true) },
  { id: "scroll_up", label: "Scroll up", group: "This page", run: (c) => scrollPage(c.host, false) },
  { id: "scroll_top", label: "Scroll to the top", group: "This page", run: (c) => scrollEdge(c.host, false) },
  { id: "scroll_bottom", label: "Scroll to the bottom", group: "This page", run: (c) => scrollEdge(c.host, true) },
  { id: "click", label: "Click a button or link", group: "This page", takesArg: "name",
    choose: () => pageLabels.show(), run: (c, r) => clickTarget(c.host, r) },
  { id: "write_in", label: "Write in a field", group: "This page", takesArg: "field",
    choose: (c) => chooseField(c.host), run: (c, r) => writeInto(c.host, r) },
  { id: "close", label: "Close", group: "This page", run: (c) => closeTop(c.host) },
  { id: "next_tab", label: "Next tab", group: "This page", run: (c) => stepTab(c.host, 1) },
  { id: "previous_tab", label: "Previous tab", group: "This page", run: (c) => stepTab(c.host, -1) },
  { id: "pick", label: "Pick a number", group: "This page", takesArg: "number", hidden: true,
    applies: () => numbersOn(), missing: "Nothing numbered to pick from",
    run: (c, r) => pickNumber(c.host, r.arg ?? r.words) },
  { id: "show_labels", label: "Show labels", group: "This page", applies: () => !pageLabels.get().on,
    missing: "Labels are showing", run: () => pageLabels.show() },
  { id: "hide_labels", label: "Hide labels", group: "This page", applies: () => pageLabels.get().on,
    missing: "No labels showing", run: () => pageLabels.hide() },
  { id: "show_commands", label: "Show commands", group: "This page", hidden: true,
    run: (c) => void showCommands(c, VOICE_ACTIONS) },
  { id: "send", label: "Send message", group: "Chat", applies: (c) => !!c.chat, missing: "No chat to send to",
    run: (c, r) => send(c.host, c.chat!, r.at) },
  { id: "new_chat", label: "New chat", group: "Chat", applies: (c) => !!c.anyChat, missing: "No chat open",
    run: (c, r) => newChat(c.host, c.anyChat!, r.at) },
  { id: "chat_model", label: "Change the model", group: "Chat", takesArg: "model", applies: (c) => !!c.chat,
    missing: "No chat to change", choose: (c) => void chooseModel(c.host, c.chat!),
    run: (c, r) => {
      if (r.arg) void chatModel(c.host, c.chat!, r.arg, r.at);
      else c.host.notice({ text: "Say a model name after chat model" });
    } },
  { id: "open_chat", label: "Show the chat beside the page", group: "Chat", applies: (c) => !!c.chat,
    missing: "No chat to open", run: (c, r) => openChatAction(c.host, c.chat!, r.at) },
  { id: "stop_reply", label: "Stop chat", group: "Chat", applies: (c) => !!c.anyChat, missing: "No chat open",
    run: (c) => void stopReply(c.host, c.anyChat!) },
  { id: "read_page", label: "Read this page", group: "Read aloud", run: (c) => readPage(c.host) },
  { id: "read_reply", label: "Read the last reply", group: "Read aloud", run: (c) => readLastReply(c.host, c.anyChat) },
  { id: "stop_reading", label: "Stop reading", group: "Read aloud", applies: () => reader.active,
    missing: "Nothing is being read", run: () => stopReading() },
  { id: "read_faster", label: "Read faster", group: "Read aloud", run: (c) => readSpeed(c.host, true) },
  { id: "read_slower", label: "Read slower", group: "Read aloud", run: (c) => readSpeed(c.host, false) },
  { id: "open", label: "Open a page", group: "Navigation", takesArg: "page", choose: (c) => choosePlace(c.host),
    describe: (_c, words) => { const t = placeNamed(words, true); return t ? `Open ${t.label}` : null; },
    run: (c, r) => { if (r.arg) openPlace(c.host, r.arg, r.words, r.at, r.loose); else notCommand(c.host, r, "Say a place after open"); } },
  { id: "go_back", label: "Go back", group: "Navigation", run: (c, r) => goBack(c.host, r.at) },
  { id: "go_forward", label: "Go forward", group: "Navigation", run: (c, r) => goForward(c.host, r.at) },
  { id: "scratch", label: "Scratch that", group: "Dictation", run: (c, r) => scratch(c.host, r.at) },
  { id: "cancel", label: "Undo the last action", group: "Dictation", applies: () => !!actionHistory.newestUndo(),
    missing: "Nothing to undo",
    run: (c) => { const e = actionHistory.newestUndo(); if (e) void undoAction(c.host, e.id); } },
  { id: "dictation_only", label: "Switch to Dictation only", group: "Dictation", applies: mode("dictation"),
    run: (c) => c.host.setMode("dictation") },
  { id: "mixed_mode", label: "Switch to Mixed", group: "Dictation", applies: mode("mixed"),
    run: (c) => c.host.setMode("mixed") },
  { id: "commands_only", label: "Switch to Commands only", group: "Dictation", applies: mode("commands"),
    run: (c) => c.host.setMode("commands") },
  { id: "start_writing", label: "Start writing", group: "Dictation", hidden: true, applies: (c) => c.host.getState().mode === "commands",
    run: (c) => c.host.setMode("mixed") },
  { id: "pause", label: "Pause dictation", group: "Dictation", applies: (c) => !c.host.getState().paused,
    run: (c) => c.host.pause(true) },
  { id: "resume", label: "Resume dictation", group: "Dictation", hidden: true, run: (c) => c.host.pause(false) },
  { id: "stop", label: "Stop dictation", group: "Dictation", run: (c) => void c.host.stop() },
];

export const ACTION_BY_ID = new Map(VOICE_ACTIONS.map((a) => [a.id, a]));

/** Whether `a` can be run now. */
export function usable(a: VoiceAction, c: ActionCtx): boolean {
  return !a.applies || a.applies(c);
}

export function closeNumbered(): boolean {
  if (!numbered.get()) return false;
  numbered.close();
  return true;
}
