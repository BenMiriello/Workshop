// A name said inside a sentence: "can you open up the speech settings", "switch
// this one to opus". The matchers for places, models and labels want every
// word of a query to fit, so a command worded freely is tried span by span,
// longest first and, among equal lengths, the later one first (the name
// usually ends the sentence), until one fits.

const MAX_WORDS = 12;

export function spansOf(text: string): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean).slice(-MAX_WORDS);
  const out: string[] = [];
  for (let len = words.length; len > 0; len--) {
    for (let at = words.length - len; at >= 0; at--) out.push(words.slice(at, at + len).join(" "));
  }
  return out;
}

export function findIn<T>(text: string, match: (query: string) => T | null): T | null {
  for (const q of spansOf(text)) {
    const got = match(q);
    if (got) return got;
  }
  return null;
}
