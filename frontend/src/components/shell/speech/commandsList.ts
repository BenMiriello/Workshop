// Commands: everything that can be done right now, grouped and numbered (the
// actions the app on screen declares under its own name), each
// with every way it is said in the current mode. Saying its number runs it.
// A command said with a name ("open", "click") shows the name as a slot
// (`open ‹page›`); run by its number it offers the names instead
// (the places, the models, the labels on the page). In Commands only mode any
// wording is understood, so the phrases are only the usual ways to say each.

import { queryClient } from "../../../lib/queryClient";
import { phrasesFor, speechCommandsQuery, type SpeechCommands } from "../../../lib/speech/commandsApi";
import { short } from "./actionRun";
import { appActionGroups, type AppActionItem } from "./appActions";
import { numbered, numberGroups, type NumberedItem } from "./numbered";
import type { ActionCtx, Group, VoiceAction } from "./voiceActionTypes";

const GROUPS: Group[] = ["This page", "Chat", "Read aloud", "Navigation", "Dictation"];

type Item = Omit<NumberedItem, "n">;

function itemOf(c: ActionCtx, a: VoiceAction, said: string[]): Item {
  const phrases = a.takesArg ? said.map((p) => `${p} ‹${a.takesArg}›`) : said;
  const run = a.takesArg
    ? a.choose && (() => a.choose!(c))
    : () => a.run(c, { arg: null, words: "", at: null, loose: false });
  return { label: a.label, hint: phrases[0], phrases, run };
}

function appItem(a: AppActionItem): Item {
  const pick = () => numbered.show({
    kind: "choices",
    title: a.label,
    groups: numberGroups([{ label: a.label, items: a.choices.map((ch) => ({ label: ch, run: () => a.run(ch) })) }]),
  });
  const run = a.choices.length ? pick : () => a.run(null);
  return { label: a.label, hint: a.phrase ?? undefined, phrases: a.phrase ? [a.phrase] : [], run };
}

function own(cmds: SpeechCommands | undefined, c: ActionCtx): Item[] {
  const mode = c.host.getState().mode;
  if (mode === "dictation") return [];
  return (cmds?.custom ?? []).filter((x) => x.when !== "off" && x.phrases.length).map((x) => {
    const phrases = x.phrases.map((p) => (mode !== "commands" && x.when === "trigger" ? `${cmds!.trigger} ${p}` : p));
    return { label: `Type "${short(x.text)}"`, hint: phrases[0], phrases, run: () => c.host.write(x.text) };
  });
}

export async function showCommands(c: ActionCtx, actions: readonly VoiceAction[]): Promise<void> {
  const cmds = await queryClient.fetchQuery(speechCommandsQuery).catch(() => undefined);
  const mode = c.host.getState().mode;
  const items = new Map<Group, Item[]>(GROUPS.map((g) => [g, []]));
  for (const a of actions) {
    if (a.hidden || (a.applies && !a.applies(c))) continue;
    const said = phrasesFor(cmds, a.id, mode);
    if (!said.length && mode !== "commands") continue;
    items.get(a.group)!.push(itemOf(c, a, said));
  }
  numbered.show({
    kind: "commands",
    title: "Commands",
    groups: numberGroups([
      { label: GROUPS[0], items: items.get(GROUPS[0])! },
      ...(mode === "dictation" ? [] : appActionGroups().map((g) => ({ label: g.label, items: g.items.map(appItem) }))),
      ...GROUPS.slice(1).map((g) => ({ label: g, items: items.get(g)! })),
      { label: "Your commands", items: own(cmds, c) },
    ]),
  });
}
