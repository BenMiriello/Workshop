// What a spoken command does (docs/dictation-and-clipboard.md, "Spoken
// commands"). The server recognises a command only when it is said on its own
// and cuts its words from the stream; here it acts, through the one list of
// actions (voiceActions.ts). Every action that changes something applies at
// once and goes into the action history (actionHistory.ts), and the bar shows
// it for five seconds with Cancel to undo it.
//
// A spoken "cancel" closes a numbered list that is showing, or else undoes the
// action said just before it (takeBacks.ts), wherever the bar has got to by
// then. Said when there is nothing to undo it is ordinary words, written as
// heard. Any other command that does not apply here (send with no chat) says so.
//
// A sentence worded freely arrives as "guess" (guessRule.ts decides): run, asked
// as numbered choices ("did you mean"), or let go. An action a page or app
// declared arrives by its id ("app:<n>") and runs through appActions.ts.

import type { Guess, LiveCommand } from "../../../lib/speech/liveWire";
import { actionHistory } from "./actionHistory";
import { short, undoAction } from "./actionRun";
import { appFits, appTitle, isAppAction, runAppAction, runAppGuess } from "./appActions";
import type { CommandHost } from "./dictation";
import { decide } from "./guessRule";
import { numbered, numberGroups } from "./numbered";
import type { Said } from "./takeBacks";
import { ACTION_BY_ID, closeNumbered, ctxOf, usable, type ActionCtx } from "./voiceActions";

function cancel(host: CommandHost, m: LiveCommand): void {
  if (closeNumbered()) return;
  const e = m.to > 0 ? actionHistory.answer(m.from) : actionHistory.newestUndo();
  if (e) void undoAction(host, e.id);
  else host.write(m.words);
}

function fits(c: ActionCtx, words: string) {
  return (g: Guess): boolean => {
    if (g.name === "type") return !!g.text;
    if (isAppAction(g.name)) return appFits(g.name, words);
    const a = ACTION_BY_ID.get(g.name);
    return !!a && usable(a, c) && (!a.describe || a.describe(c, words) !== null);
  };
}

function runGuessed(c: ActionCtx, g: Guess, words: string, at: Said | null): void {
  if (g.name === "type") { if (g.text) c.host.write(g.text); return; }
  if (isAppAction(g.name)) { runAppGuess(c.host, g.name, words); return; }
  ACTION_BY_ID.get(g.name)?.run(c, { arg: words, words, at, loose: true });
}

function guessed(host: CommandHost, m: LiveCommand, at: Said | null): void {
  if (m.loading) {
    host.keepUnwritten(m.words);
    host.notice({ text: "Still loading the command model" });
    return;
  }
  const c = ctxOf(host);
  const d = decide(m.guesses ?? [], m.none ?? 1, fits(c, m.words));
  if (d.kind === "act") { runGuessed(c, d.guess, m.words, at); return; }
  if (d.kind === "none") {
    host.keepUnwritten(m.words);
    host.notice({ text: `No command: "${short(m.words)}"` });
    return;
  }
  numbered.show({
    kind: "choices",
    title: "Did you mean",
    groups: numberGroups([{
      label: `"${short(m.words)}"`,
      items: d.guesses.map((g) => {
        const a = ACTION_BY_ID.get(g.name);
        const label = g.name === "type" ? `Type "${short(g.text ?? "")}"`
          : a?.describe?.(c, m.words) ?? a?.label ?? appTitle(g.name) ?? g.name;
        return { label, run: () => runGuessed(ctxOf(host), g, m.words, null) };
      }),
    }]),
  });
}

export function runCommand(host: CommandHost, m: LiveCommand): void {
  const at = m.to > 0 ? { to: m.to, heard: Date.now() } : null;
  if (m.name === "cancel") { cancel(host, m); return; }
  if (m.name === "guess") { guessed(host, m, at); return; }
  if (m.name === "type") { if (m.arg) host.write(m.arg); return; }
  if (isAppAction(m.name)) { runAppAction(host, m.name, m.arg, m.words); return; }
  const a = ACTION_BY_ID.get(m.name);
  if (!a) return;
  const c = ctxOf(host);
  if (!usable(a, c)) { host.notice({ text: a.missing ?? `${a.label} does not apply here` }); return; }
  a.run(c, { arg: m.arg, words: m.words, at, loose: false });
}
