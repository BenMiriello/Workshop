// How words are joined onto a chat's draft, and how a taken-back message is
// put back. Pure, so the rules run in a test; draftSync and chatTargets both
// use them, which is what keeps every tab arriving at the same text.

/** `add` placed at the end of `base`. A space goes between two words; nothing
 *  goes in when either side already brings its own whitespace (a dictated line
 *  break arrives as "\n"). */
export function joinAppend(base: string, add: string): string {
  if (!add) return base;
  if (!base) return add.replace(/^ +/, "");
  if (/\s$/.test(base) || /^\s/.test(add)) return base + add;
  return `${base} ${add}`;
}

/** `base` with the words at its end, `was`, swapped for `now`; when it no
 *  longer ends with `was` (it was edited), `now` is added instead. */
export function swapTail(base: string, was: string, now: string): string {
  const stem = was && base.endsWith(was) ? base.slice(0, -was.length).replace(/ $/, "") : base;
  return now ? joinAppend(stem, now) : stem;
}

/** The draft after a cancel: the sent text, then a line break and whatever
 *  was typed into the draft since, when there is anything. */
export function restoreText(sent: string, typedSince: string): string {
  const since = typedSince.replace(/^\s+/, "");
  if (!since.trim()) return sent;
  if (!sent) return since;
  return `${sent}\n${since}`;
}

/** The sent files first, then any attached since, each once. */
export function restoreAttachments<T extends { id: string }>(sent: T[], since: T[]): T[] {
  const seen = new Set(sent.map((a) => a.id));
  return [...sent, ...since.filter((a) => !seen.has(a.id))];
}
