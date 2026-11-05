// "Open <name>" as an action in the history: go to the place or page of the
// shell the name means (lib/speech/openPlaces.ts). Undo goes back to the page
// before, redo opens it again. A name that is no place may be something on
// the screen ("open the second picture"), which is clicked. One that fits
// nothing at all was not a command ("open the window"), so its words are
// written as said, or, with only commands heard, the bar says nothing
// matched. Worded freely ("can you bring up the speech settings"), the name
// is looked for inside the sentence.

import { matchOpen, openPlaces, openTargets, type OpenTarget } from "../../../lib/speech/openPlaces";
import { findIn } from "../../../lib/speech/spans";
import { placeFor } from "../places";
import type { WorkshopShellWindow } from "../Shell";
import { recordAction, short } from "./actionRun";
import type { CommandHost } from "./dictation";
import { numbered, numberGroups } from "./numbered";
import { clickSaid } from "./pageActions";
import type { Said } from "./takeBacks";

const here = () => location.pathname + location.search;
const go = (to: string) => (window as WorkshopShellWindow).workshopNavigate?.(to);

function nameOf(path: string): string {
  const list = openPlaces.get();
  return list.length ? placeFor([...list], path.split("?")[0]).name : path;
}

/** The place `query` names; `loose`, somewhere inside it. */
export function placeNamed(query: string, loose: boolean): OpenTarget | null {
  const targets = openTargets(openPlaces.get(), location.pathname);
  return matchOpen(query, targets) ?? (loose ? findIn(query, (q) => matchOpen(q, targets)) : null);
}

export function openPlace(host: CommandHost, query: string, words: string, at: Said | null, loose = false): void {
  const t = placeNamed(query, loose);
  if (!t) { void openOnScreen(host, query, words, loose); return; }
  const was = here();
  if (was === t.to) { host.notice({ text: `Already on ${short(t.label)}` }); return; }
  go(t.to);
  recordAction(host, {
    label: `Opened ${short(t.label)}`,
    undo: async () => { go(was); return { ok: true, text: `Back to ${short(nameOf(was))}` }; },
    redo: async () => { go(t.to); return { ok: true, text: `Opened ${short(t.label)}` }; },
  }, at);
}

// No place by that name: something on screen ("open the second picture"),
// else not a command after all.
async function openOnScreen(host: CommandHost, query: string, words: string, loose: boolean): Promise<void> {
  if (await clickSaid(host, query)) return;
  if (host.getState().mode === "commands" || loose) host.notice({ text: `Nothing called "${short(query)}"` });
  else host.write(words);
}

/** The places "open" reaches as numbered choices. */
export function choosePlace(host: CommandHost): void {
  const places = openTargets(openPlaces.get(), location.pathname).filter((t) => t.rank === 0);
  if (!places.length) { host.notice({ text: "No places to open" }); return; }
  numbered.show({
    kind: "choices",
    title: "Open",
    groups: numberGroups([{ label: "Places", items: places.map((t) => ({ label: t.label, run: () => openPlace(host, t.label, t.label, null) })) }]),
  });
}
