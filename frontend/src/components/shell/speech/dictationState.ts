// What the dictation controller shows: one value every surface reads.

import { MicError } from "../../../lib/voice/errors";
import type { SpeechMode } from "../../../lib/speech/commandsApi";
import type { ChatPlace, Destination, FieldPlace } from "./destination";
import type { DictationPiece } from "./transcript";

export type DictationPhase = "idle" | "starting" | "listening" | "finishing" | "error";

/** What the bar shows for a few seconds: an action just taken, with Cancel to
 *  undo it, or how one went. */
export type Notice = {
  id: number;
  text: string;
  /** Where the operation can be seen (the chat it happened in). */
  href?: string | null;
  /** When it was shown, and when it goes on its own (ms since the epoch);
   *  `until` null stays until replaced. */
  since: number;
  until: number | null;
  cancel?: () => void;
  /** Words the clipboard could not take on its own: the bar offers Copy. */
  copy?: string;
  /** One more thing the notice offers ("Bring last words"). */
  action?: { label: string; run: () => void };
};

export type DictationState = {
  phase: DictationPhase;
  /** Listening, but writing nothing until resumed. */
  paused: boolean;
  /** How spoken commands are heard (dictationMode.ts); null until read. */
  mode: SpeechMode | null;
  /** Microphone level, 0 to 1. Display only. */
  level: number;
  /** Connected to the speech server. Words wait on the device while not. */
  connected: boolean;
  /** Was connected at some point in this dictation, so a gap is a reconnect. */
  everConnected: boolean;
  /** Where words go. Chosen at the start; changed only by the operator. */
  into: Destination | null;
  /** `into` is a field that left the page; its words wait for it. */
  waiting: boolean;
  /** Settled words of this dictation, for the count and the newest words. */
  said: string;
  /** Where each run of them went (transcript.ts). */
  pieces: DictationPiece[];
  /** Words still being said, not shown in a field on screen. */
  open: string;
  /** Every word still being said, wherever it is shown. */
  pending: string;
  /** What the clipboard or a waiting field holds of this dictation now. */
  kept: string | null;
  /** A field focused now that is not where words go: "Dictate here". */
  here: FieldPlace | ChatPlace | null;
  notice: Notice | null;
  startedAt: number | null;
  error: string | null;
  /** The microphone was refused; the note stays until dismissed. */
  blocked: boolean;
  /** The field dictation started in, which errors are shown against. */
  originEl: HTMLElement | null;
};

export const IDLE: DictationState = {
  phase: "idle", paused: false, mode: null, level: 0, connected: false, everConnected: false, into: null, waiting: false,
  said: "", pieces: [], open: "", pending: "", kept: null, here: null, notice: null, startedAt: null, error: null, blocked: false, originEl: null,
};

export function messageOf(e: unknown): string {
  if (e instanceof MicError) return e.message;
  const m = e instanceof Error ? e.message : String(e);
  return m || "Dictation failed.";
}

/** "12 words". */
export function wordCount(text: string): string {
  const n = text.trim() ? text.trim().split(/\s+/).length : 0;
  return n === 1 ? "1 word" : `${n} words`;
}
