// The spoken commands, kept on the server per user so every device hears the
// same ones: the mode (services/speech/modes.py) and the trigger word, the built-ins
// with their phrases and when each is heard, the operator's own, each of
// which types a fixed text, and how page labels are drawn.

import { useQuery } from "@tanstack/react-query";
import type { components } from "../../api/schema.gen";
import { jsonOrFail } from "../http";
import { queryClient } from "../queryClient";
import { queryKeys } from "../queryKeys";

export type SpeechCommands = components["schemas"]["SpeechCommands"];
export type SpeechCommand = components["schemas"]["SpeechCommand"];
export type CustomSpeechCommand = components["schemas"]["CustomSpeechCommand"];
export type SpeechMode = SpeechCommands["mode"];
export type CommandWhen = SpeechCommand["when"];
type Update = components["schemas"]["SpeechCommandsUpdate"];
type ModeChange = components["schemas"]["SpeechModeChange"];
export type SpeechLabels = components["schemas"]["SpeechLabels"];

const URL = "/api/speech/commands";

export const speechCommandsQuery = {
  queryKey: queryKeys.speech.commands(),
  queryFn: (): Promise<SpeechCommands> => fetch(URL).then((r) => jsonOrFail<SpeechCommands>(r)),
  staleTime: 5 * 60_000,
};

export function useSpeechCommands() {
  return useQuery(speechCommandsQuery);
}

function settle(r: Promise<Response>): Promise<SpeechCommands> {
  return r.then((res) => jsonOrFail<SpeechCommands>(res)).then((saved) => {
    queryClient.setQueryData(queryKeys.speech.commands(), saved);
    return saved;
  });
}

export function saveSpeechCommands(c: SpeechCommands): Promise<SpeechCommands> {
  const body: Update = {
    builtins: c.builtins.map((b) => ({ id: b.id, phrases: b.phrases, when: b.when })),
    custom: c.custom,
  };
  return settle(fetch(URL, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

/** Change the mode, and the trigger word when given. "writing" goes back to
 *  the mode words were last written in. */
export function saveSpeechMode(mode: ModeChange["mode"], trigger?: string): Promise<SpeechCommands> {
  const body: ModeChange = { mode, ...(trigger !== undefined ? { trigger } : {}) };
  return settle(fetch(`${URL}/mode`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

export function saveSpeechLabels(labels: SpeechLabels): Promise<SpeechCommands> {
  return settle(fetch(`${URL}/labels`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(labels) }));
}

/** The most labels a page can show (services/speech/command_list.py NUMBER_MAX),
 *  and the fewest the operator can ask for. */
export const LABELS_RANGE = { min: 10, max: 250 } as const;

export function resetSpeechCommands(): Promise<SpeechCommands> {
  return settle(fetch(`${URL}/reset`, { method: "POST" }));
}

// The actions heard in dictation mode (services/speech/modes.py IN_DICTATION).
const IN_DICTATION = new Set(["dictation_only", "mixed_mode", "commands_only", "start_writing",
  "pause", "resume", "stop"]);

/** Whether a command is listened for at all in `mode`. */
export function heardIn(mode: SpeechMode, id: string, inline = false): boolean {
  if (inline) return mode !== "commands";
  return mode !== "dictation" || IN_DICTATION.has(id);
}

/** How a command is said now, for showing beside what it does: its first
 *  phrase, after the trigger word when the command needs it in `mode` (the
 *  saved mode unless given). Null when it is not heard at all. */
export function phraseFor(c: SpeechCommands | undefined, id: string, mode?: SpeechMode | null): string | null {
  return phrasesFor(c, id, mode)[0] ?? null;
}

/** Every way a command is said now, in the order its settings list them. */
export function phrasesFor(c: SpeechCommands | undefined, id: string, mode?: SpeechMode | null): string[] {
  const b = c?.builtins.find((x) => x.id === id);
  const m = mode ?? c?.mode;
  if (!c || !b || !m || b.when === "off" || !heardIn(m, id, b.kind === "inline")) return [];
  return b.phrases.map((p) => (m !== "commands" && b.when === "trigger" ? `${c.trigger} ${p}` : p));
}

/** The modes, in the order the pickers list them. */
export const SPEECH_MODES: { id: SpeechMode; label: string }[] = [
  { id: "dictation", label: "Dictation only" },
  { id: "mixed", label: "Mixed" },
  { id: "commands", label: "Commands only" },
];

export const WHEN_LABELS: Record<CommandWhen, string> = {
  alone: "Say it directly",
  trigger: "Say the trigger word first",
  off: "Off",
};

/** Shown on a command's menu button when it is not heard directly. */
export const WHEN_SHORT: Record<CommandWhen, string> = {
  alone: "",
  trigger: "trigger word",
  off: "off",
};

export function modeLabel(mode: SpeechMode): string {
  return SPEECH_MODES.find((m) => m.id === mode)?.label ?? mode;
}
