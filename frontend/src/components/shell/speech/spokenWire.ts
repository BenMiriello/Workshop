// What the live socket is told about what can be said right now: every action
// the open pages declare (appActionRegistry.ts), and the ids of the commands
// and actions that do not apply here. The server hears the phrases of those as
// plain words, so a command that could only say "does not apply" never eats
// dictation, and leaves them out when it understands a loose wording.
//
// Whether a command applies is asked of the one list of actions
// (voiceActions.ts). Its inputs (where words go, the mode, the reader, a
// numbered list or labels, an open chat, the page shown) change in many
// places, so the answer is worked out again after any of them and once a
// second, and sent only when it differs.

import type { LiveActions } from "../../../lib/speech/liveWire";
import { actionHistory } from "./actionHistory";
import { appActionsNow, appActionsWire, subscribeAppActions } from "./appActionRegistry";
import { dictation } from "./dictation";
import { pageLabels } from "./labels/pageLabels";
import { numbered } from "./numbered";
import { reader } from "./reader";
import { ctxOf, usable, VOICE_ACTIONS } from "./voiceActions";

const SETTLE_MS = 150;
const AGAIN_MS = 1000;

/** The actions message for now. "cancel" also closes a numbered list. */
export function spokenNow(): LiveActions {
  const c = ctxOf(dictation);
  const off = VOICE_ACTIONS.filter((a) => !usable(a, c) && !(a.id === "cancel" && numbered.get()))
    .map((a) => a.id);
  const list = appActionsWire();
  const on = new Set(appActionsNow().map((e) => e.id));
  return { list, off: [...off, ...list.filter((a) => !on.has(a.id)).map((a) => a.id)] };
}

/** Call `send` with the actions message now and whenever it changes, until
 *  the returned function is called. */
export function watchSpoken(send: (m: LiveActions) => void): () => void {
  let last = "";
  let timer: ReturnType<typeof setTimeout> | null = null;
  const check = () => {
    timer = null;
    const m = spokenNow();
    const key = JSON.stringify(m);
    if (key !== last) { last = key; send(m); }
  };
  const soon = () => { if (!timer) timer = setTimeout(check, SETTLE_MS); };
  const offs = [
    dictation.subscribe(soon), numbered.subscribe(soon), pageLabels.subscribe(soon),
    reader.subscribe(soon), actionHistory.subscribe(soon), subscribeAppActions(soon),
  ];
  const every = setInterval(check, AGAIN_MS);
  check();
  return () => {
    offs.forEach((off) => off());
    clearInterval(every);
    if (timer) clearTimeout(timer);
  };
}
