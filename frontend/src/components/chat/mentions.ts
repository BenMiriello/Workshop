// The @-mention being typed in the composer.
//
// Lifted out of the old slashCommands module when the slash catalogue was
// deleted. Mentions survived that removal because they are a different thing:
// a mention names an AGENT to answer, which nothing else in the UI can express,
// while every slash command was either a duplicate of a visible control or, in
// most cases, measured not to run at all.

// The @-mention being typed: the last "@word" run at the very END of the
// composer. Anchored to the end so editing earlier in a message never re-opens
// the picker, and suppressed once a complete mention exists because only the
// FIRST tag in a message is ever parsed server-side — addressing two agents in
// one breath has no coherent meaning.
const MENTION_TAIL = /(?:^|\s)@([a-z0-9-]*)$/;
const MENTION_COMPLETE = /(?:^|\s)@[a-z][a-z0-9-]*\s/;

export function matchMention<T extends { slug: string; name: string }>(
  input: string,
  candidates: T[],
): { query: string | null; matches: T[]; show: boolean } {
  const tail = MENTION_TAIL.exec(input);
  const query = tail && !MENTION_COMPLETE.test(input) ? tail[1] : null;
  const matches = query === null
    ? []
    : candidates.filter((a) => a.slug.startsWith(query) || a.name.toLowerCase().startsWith(query));
  return { query, matches, show: query !== null && matches.length > 0 };
}

// Replace the partial token in place: everything typed around it is preserved,
// and the trailing space means the next keystroke is the message rather than
// more of the name.
export function applyMention(input: string, slug: string): string {
  return input.replace(/@[a-z0-9-]*$/, `@${slug} `);
}
