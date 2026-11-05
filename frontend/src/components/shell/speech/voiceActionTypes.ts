// The shape of an entry in the list of what dictation can do by voice
// (voiceActions.ts).

import type { ChatTarget } from "../../../lib/speech/chatTargets";
import type { CommandHost } from "./dictation";
import type { Said } from "./takeBacks";

export type ActionCtx = { host: CommandHost; chat: ChatTarget | null; anyChat: ChatTarget | null };
export type Run = { arg: string | null; words: string; at: Said | null; loose: boolean };
export type Group = "This page" | "Chat" | "Read aloud" | "Navigation" | "Dictation";

export type VoiceAction = {
  id: string;
  label: string;
  group: Group;
  /** Said with a name after it: what the name is, shown in the phrase as
   *  `open ‹place›`. */
  takesArg?: string;
  /** Picked from the Commands list rather than said with a name: offer the
   *  names to choose from. */
  choose?: (c: ActionCtx) => void;
  applies?: (c: ActionCtx) => boolean;
  /** Said when it is asked for and does not apply. */
  missing?: string;
  /** What it would do with a sentence, for a numbered choice; null when it
   *  would do nothing with it. */
  describe?: (c: ActionCtx, words: string) => string | null;
  run: (c: ActionCtx, r: Run) => void;
  /** Never listed in Commands. */
  hidden?: boolean;
};
