// Where the fold/clear divider renders relative to the message list.
//
// A forced /compact always keeps _COMPACT_KEEP_TAIL turns verbatim, so the
// boundary always lands ON a message index and the divider draws inline in
// the map. /clear cuts everything — the boundary lands AT messages.length,
// past the last real index — so a divider drawn only inline in the map never
// renders at all for a clear. This is the rule behind that, extracted so it
// can be tested without a DOM harness (see turnBatching.ts for the pattern).

export type ContextCutMark = { atIndex: number } | { trailing: true } | null;

export function contextCutMark(compactedThrough: number, messageCount: number): ContextCutMark {
  if (compactedThrough <= 0) return null;
  if (compactedThrough >= messageCount) return { trailing: true };
  return { atIndex: compactedThrough };
}
