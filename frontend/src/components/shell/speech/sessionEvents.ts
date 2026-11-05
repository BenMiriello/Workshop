// How the dictation controller hears its live session: words to the word
// delivery, commands to the command runner, the level and the connection to
// the bar. Anything from a session that is no longer the current one is
// dropped, so a Stop or a restart never mixes two dictations.

import type { LiveCommand } from "../../../lib/speech/liveWire";
import type { DictationState } from "./dictationState";
import type { DictationWords } from "./dictationWords";
import type { LiveEvents } from "./liveSession";

export type SessionSide = {
  current: () => boolean;
  words: DictationWords;
  /** Something was said: the silence stop starts counting again. */
  spoke: () => void;
  state: () => DictationState;
  set: (patch: Partial<DictationState>) => void;
  command: (m: LiveCommand) => void;
  paused: (paused: boolean) => void;
  mode: (mode: string, said: boolean) => void;
  stop: () => void;
  unwritten: (text: string) => void;
  taken: () => void;
};

export function sessionEvents(c: SessionSide): LiveEvents {
  return {
    onText: (text, from, to) => {
      if (!c.current()) return;
      if (text !== c.state().open) c.spoke();
      c.words.text(text, from, to);
    },
    // Settling words that were already shown is not new speech: it can come
    // seconds after the last word was said.
    onFinal: (m) => {
      if (!c.current()) return;
      c.words.final(m);
    },
    onCommand: (m) => {
      if (!c.current()) return;
      c.spoke();
      c.words.command(m);
      c.command(m);
    },
    onPaused: (paused) => { if (c.current()) c.paused(paused); },
    onUnwritten: (text) => { if (c.current()) c.unwritten(text); },
    onTaken: () => { if (c.current()) c.taken(); },
    onMode: (mode, said) => { if (c.current()) c.mode(mode, said); },
    onLevel: (v) => {
      const shown = c.state().level;
      const next = v > shown ? v : shown * 0.85 + v * 0.15;
      if (Math.abs(next - shown) >= 0.03) c.set({ level: next });
    },
    onConnected: (connected) => {
      if (c.current()) c.set({ connected, everConnected: c.state().everConnected || connected });
    },
    onMicLost: () => c.stop(),
    onError: (message) => { if (c.current()) c.set({ error: message }); },
  };
}
