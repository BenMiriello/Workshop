// What a page's or app's own spoken action does when it is heard, or picked in
// Commands (appActionRegistry.ts keeps them). It runs at once, and the bar
// says so; it has no undo the shell could offer, so one marked consequential
// (`consequentialHint`: it sends, deletes or spends) is asked first as a
// numbered choice. The words said for its input are its argument: the choice
// they name, or the words themselves.

import type { CommandHost } from "./dictation";
import { short } from "./actionRun";
import { afterPhrase, argFor, asksFirst, shownPhrase } from "./appActionsCore";
import { appAction, appActionsNow, placeLabel, type AppEntry } from "./appActionRegistry";
import { dictation } from "./dictation";
import { numbered, numberGroups } from "./numbered";

export { subscribeAppActions } from "./appActionRegistry";

export const isAppAction = (name: string) => name.startsWith("app:");

async function apply(host: CommandHost, e: AppEntry, arg: string | undefined): Promise<void> {
  const title = e.action.title;
  host.notice({ text: arg ? `${title}: ${short(arg)}` : title });
  try {
    await e.action.run(arg);
  } catch (err) {
    host.notice({ text: `${title} failed${err instanceof Error && err.message ? `: ${short(err.message)}` : ""}` });
  }
}

/** Run action `id` with the words said for its input. `words`: everything
 *  said, written back when the action is gone and words are being written. */
export function runAppAction(host: CommandHost, id: string, said: string | null, words = ""): void {
  const e = appAction(id);
  if (!e) {
    if (words && host.getState().mode !== "commands") host.write(words);
    else host.notice({ text: "That does not apply here" });
    return;
  }
  const a = argFor(e.action, said);
  if (!a.ok) { host.notice({ text: a.missing }); return; }
  if (!asksFirst(e.action)) { void apply(host, e, a.arg); return; }
  const label = a.arg ? `${e.action.title}: ${short(a.arg)}` : e.action.title;
  numbered.show({
    kind: "choices",
    title: `Run ${label}?`,
    groups: numberGroups([{ label: placeLabel(e.doc), items: [
      { label: `Run ${label}`, run: () => { const now = appAction(id); if (now) void apply(host, now, a.arg); } },
      { label: "Cancel", run: () => {} },
    ] }]),
  });
}

/** Whether action `id` fits a sentence worded freely: it applies, and an
 *  input with choices finds one named in it. */
export function appFits(id: string, words: string): boolean {
  const e = appAction(id);
  if (!e) return false;
  return !e.action.input?.choices?.length || argFor(e.action, words).ok;
}

/** Run action `id` for a sentence worded freely: a choice named anywhere in
 *  it, or free text after one of its phrases. */
export function runAppGuess(host: CommandHost, id: string, words: string): void {
  const e = appAction(id);
  const choices = e?.action.input?.choices?.length;
  runAppAction(host, id, e?.action.input ? (choices ? words : afterPhrase(words, e.action)) : null);
}

/** What a numbered choice calls action `id`. */
export const appTitle = (id: string): string | null => appAction(id)?.action.title ?? null;

export type AppActionItem = {
  id: string; label: string; phrase: string | null; takesArg: boolean; choices: readonly string[];
  run: (arg: string | null) => void;
};

/** The actions that apply now, grouped under the place they belong to.
 *  `run` runs one as if it was said, with `arg` as the words for its input. */
export function appActionGroups(): { label: string; items: AppActionItem[] }[] {
  const groups = new Map<string, AppActionItem[]>();
  for (const e of appActionsNow()) {
    const label = placeLabel(e.doc);
    const items = groups.get(label) ?? [];
    items.push({
      id: e.id, label: e.action.title, phrase: shownPhrase(e.action), takesArg: !!e.action.input,
      choices: e.action.input?.choices ?? [],
      run: (arg) => runAppAction(dictation, e.id, arg),
    });
    groups.set(label, items);
  }
  return [...groups].map(([label, items]) => ({ label, items }));
}
