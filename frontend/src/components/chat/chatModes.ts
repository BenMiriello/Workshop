import { MessageSquare, Play, Ticket } from "lucide-react";

// How a chat handles work, as the UI says it.
//
// ONE DEFINITION. The label used to be written inline in the trigger and again
// in each menu item, which is how a control ends up describing itself two ways.
//
// The stored values are unchanged (`direct` / `orchestrated`) so no thread needs
// migrating — only what the operator reads changes. "Orchestrated" was a word
// about the machinery; "Tickets" is the answer to the question the control
// actually asks, which is where the work goes. Each label names its icon and
// each icon names its label.
//
// `auto` joined them on 2026-08-15. The control was a two-way switch drawn as a
// three-way choice: "draft it and ask me" and "file it and run it" are different
// answers to the same question and the second had no value to store.

export type ChatModeId = "direct" | "orchestrated" | "auto";

export type ChatModeDef = {
  id: ChatModeId;
  // The NAME, for a control that is already labelled by its context: a
  // Segmented in Settings under "new chats", a read-only value beside it.
  label: string;
  // The same choice written as what it DOES, for a list of options where each
  // row has to stand on its own. Settings asks "which mode"; the Work panel
  // asks "when work comes up", and the answer to that is a sentence, not a
  // noun. Two phrasings of one meaning, in one place, rather than one phrasing
  // stretched over two jobs or two phrasings in two files.
  action: string;
  // Shown on hover, not printed beside the name. A description that sits in the
  // row competes with the name for width and wins, which left the menu showing
  // only glosses.
  title: string;
  Icon: typeof MessageSquare;
};

export const CHAT_MODES: ChatModeDef[] = [
  {
    id: "direct",
    label: "Direct",
    action: "Keep it in chat",
    title: "Work stays in this conversation. The transcript is the record.",
    Icon: MessageSquare,
  },
  {
    id: "orchestrated",
    label: "Tickets",
    action: "Draft, then ask me",
    title: "Work leaves as tickets run by agents, and is reported back here. The ticket is the record.",
    Icon: Ticket,
  },
  {
    id: "auto",
    label: "Auto",
    action: "File and run on its own",
    title: "Work is filed and started without asking. It stops only for a question it cannot answer.",
    Icon: Play,
  },
];

// Direct is the default, matching services/chat_store.DEFAULT_CHAT_MODE. Anything
// unrecognised resolves to it rather than rendering a blank control.
export function chatModeDef(mode: string | undefined): ChatModeDef {
  return CHAT_MODES.find((m) => m.id === mode) ?? CHAT_MODES[0];
}
