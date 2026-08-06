import { MessageSquare, Ticket } from "lucide-react";

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

export type ChatModeId = "direct" | "orchestrated";

export type ChatModeDef = {
  id: ChatModeId;
  label: string;
  hint: string;
  Icon: typeof MessageSquare;
};

export const CHAT_MODES: ChatModeDef[] = [
  { id: "direct", label: "Direct", hint: "stays in this chat", Icon: MessageSquare },
  { id: "orchestrated", label: "Tickets", hint: "becomes tickets", Icon: Ticket },
];

// Direct is the default, matching services/chat_store.DEFAULT_CHAT_MODE. Anything
// unrecognised resolves to it rather than rendering a blank control.
export function chatModeDef(mode: string | undefined): ChatModeDef {
  return CHAT_MODES.find((m) => m.id === mode) ?? CHAT_MODES[0];
}
