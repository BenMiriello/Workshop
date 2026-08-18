// The pure rule behind ChatMarkdown: find the `workshop:<kind>/...` ref tokens
// in a block of text so they can be batch-resolved. Kept in its own module with
// no React/kit imports so it can be executed in the repo's node test run (there
// is no DOM harness).
//
// Matched the same way the backend extracts them (services/refs.py _TOKEN_RE):
// a token stops at whitespace and at the closing bracket/paren/quote of a
// markdown link, so `[label](workshop:doc/x.md)` yields `workshop:doc/x.md` and
// a trailing period in prose is not swallowed.
const REF_TOKEN = /workshop:[a-z]+\/[^\s)\]"'<>]+/g;

export function extractRefs(text: string): string[] {
  const found = text.match(REF_TOKEN);
  return found ? Array.from(new Set(found)) : [];
}
