// The mode while dictating (services/speech/modes.py): dictation, where
// everything is written; mixed, where words are written and commands heard as
// set; commands, where nothing is written and any wording is understood. Read
// from the saved setting when dictation starts, changed from the bar or by
// voice ("command mode", "dictation mode", "mixed mode", "start writing"), and
// saved whichever way it changed, so the next dictation starts in it.
//
// Going back to writing by voice writes into the field focused then, if there
// is one; otherwise words go where they went before.

import { queryClient } from "../../../lib/queryClient";
import { saveSpeechMode, speechCommandsQuery, type SpeechMode } from "../../../lib/speech/commandsApi";
import type { DictationState } from "./dictationState";
import type { Destination } from "./destination";

export type ModeSide = {
  state: () => DictationState;
  set: (patch: Partial<DictationState>) => void;
  /** Tell the live session; nothing when none is running. */
  send: (mode: SpeechMode) => void;
  /** What has been shown so far stays where it is. */
  freeze: () => void;
  chooseDestination: (d: Destination) => void;
};

const isMode = (m: string): m is SpeechMode => ["dictation", "mixed", "commands"].includes(m);

export class DictationMode {
  constructor(private c: ModeSide) {}

  /** The saved mode, for a dictation starting. */
  load(): void {
    void queryClient.fetchQuery(speechCommandsQuery).then((cmds) => {
      if (this.c.state().mode === null) this.c.set({ mode: cmds.mode });
    }, () => {});
  }

  /** Chosen on the bar. */
  choose(mode: SpeechMode): void {
    this.apply(mode);
    this.c.send(mode);
    void saveSpeechMode(mode).catch(() => {});
  }

  /** The server says the mode changed; `said`, by a spoken switch. */
  heard(mode: string, said: boolean): void {
    if (!isMode(mode)) return;
    const was = this.c.state().mode;
    this.apply(mode);
    if (!said) return;
    void saveSpeechMode(mode).catch(() => {});
    const here = this.c.state().here;
    if (was === "commands" && mode !== "commands" && here) this.c.chooseDestination(here);
  }

  private apply(mode: SpeechMode): void {
    if (mode === this.c.state().mode) return;
    if (mode === "commands") this.c.freeze();
    this.c.set({ mode });
  }
}
