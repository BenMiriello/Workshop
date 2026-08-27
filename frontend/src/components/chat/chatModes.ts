import { MessageSquare, Play } from "lucide-react";

// How a chat handles work, as the UI says it.
//
// ONE DEFINITION. The label used to be written inline in the trigger and again
// in each menu item, which is how a control ends up describing itself two ways.
// Each label names its icon and each icon names its label.
//
// TWO CHOICES, because the control asks one question: does work leave this
// conversation? It offered three between 2026-08-15 and 2026-08-27, when
// `orchestrated` ("file it, then wait for me") and `auto` ("file it and run
// it") turned out to be one behaviour under two names — they differed only in
// the prompt sentence the backend emitted, never in what the chat could do.
// The operator has no intended distinction between them, so `auto` absorbed
// `orchestrated`: it is the behaviour in use and the value on more threads.
//
// The retired id is still stored on chats that were in that mode, and it is
// mapped forward here rather than migrated away — see `chatModeDef`, and
// `chat_store._clamp_mode` for the same mapping on the server.

export type ChatModeId = "direct" | "auto";

// Not a `ChatModeId`: it is what the SERVER may still hand back, not something
// this UI can be set to.
const LEGACY_CHAT_MODES: Record<string, ChatModeId> = { orchestrated: "auto" };

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
    id: "auto",
    label: "Auto",
    action: "File and run on its own",
    title: "Work leaves as tickets run by agents, and is reported back here. The ticket is the record. It is filed and started without asking, and stops only for a question it cannot answer.",
    Icon: Play,
  },
];

// Direct is the default, matching services/chat_store.DEFAULT_CHAT_MODE. Anything
// unrecognised resolves to it rather than rendering a blank control.
//
// A RETIRED ID IS NOT UNRECOGNISED. A chat stored as `orchestrated` resolves to
// Auto, because that is the mode it has been running in; letting it fall to the
// default instead would show "Direct" on a chat the server is still filing from,
// which is the control lying about what the next message will do.
export function chatModeDef(mode: string | undefined): ChatModeDef {
  const id = (mode && LEGACY_CHAT_MODES[mode]) ?? mode;
  return CHAT_MODES.find((m) => m.id === id) ?? CHAT_MODES[0];
}
