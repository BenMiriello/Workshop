// Text into the pieces read aloud one request at a time.
//
// The speech endpoint takes one sentence or a short paragraph per call, and the
// player fetches the next piece while the current one plays. So a piece should
// be a sentence: short enough that the first one comes back fast, long enough
// that the voice keeps its phrasing. Very short fragments ("Okay.", "1.") are
// folded into the next sentence, and a run-on longer than `max` is cut at a
// clause boundary, else at a word.
//
// `speakableText` runs first on anything that may be markdown (a chat message
// is), so the voice does not read out asterisks, fences and link targets.

const ABBREVIATIONS = new Set([
  "mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "eg", "ie", "approx", "no", "fig", "jr", "sr",
]);

// Code goes first, so nothing inside it can pair with markup outside it. A
// fenced block is dropped (code read aloud is noise); an inline span keeps its
// words and is set aside until the end, so `Promise<void>` and `snake_case`
// survive the rules below. A tag is only a tag in a tag's shape: a closing or
// self-closing tag anywhere, an opening one only where no word runs into it,
// so `x < 3`, `List<String>` and `a<b and c>d` are read as written.
const FENCE = /```[\s\S]*?(```|$)/g;
const INLINE_CODE = /`([^`\n]*)`/g;
const HELD = /\u0000(\d+)\u0000/g;
const COMMENT = /<!--[\s\S]*?-->/g;
const AUTOLINK = /<(?:https?|mailto):[^>\s]*>/gi;
const ENDING_TAG = /<\/[a-zA-Z][\w-]*\s*>|<[a-zA-Z][\w-]*(?:\s+[^<>\n]*?)?\s*\/>/g;
const OPEN_TAG = /(?<!\w)<[a-zA-Z][\w-]*(?:\s+[^<>\n]*?)?\s*\/?>/g;

/** Markdown reduced to the words a listener should hear. */
export function speakableText(md: string): string {
  const held: string[] = [];
  return md
    .replace(FENCE, "\n")
    .replace(INLINE_CODE, (_, code: string) => `\u0000${held.push(code) - 1}\u0000`)
    .replace(COMMENT, " ")
    .replace(AUTOLINK, " ")
    .replace(ENDING_TAG, " ")
    .replace(OPEN_TAG, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")
    .replace(/^\s*\|?\s*:?-{3,}.*$/gm, "")
    .replace(/\|/g, " ")
    .replace(/(\*\*|__|~~)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])\*(\S(?:.*?\S)?)\*(?!\w)/g, "$1$2")
    .replace(/(^|\W)_(\S(?:.*?\S)?)_(?!\w)/g, "$1$2")
    .replace(HELD, (_, i: string) => held[Number(i)] ?? "")
    .replace(/[ \t]+/g, " ")
    .split("\n").map((l) => l.trim()).join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const MIN_PIECE = 12;

function isAbbreviation(chunk: string): boolean {
  const m = /(\S+)\.$/.exec(chunk);
  if (!m) return false;
  const word = m[1].toLowerCase().replace(/^[("'[]+/, "");
  // A single letter before the period is an initial ("J. Smith").
  return ABBREVIATIONS.has(word) || /^[a-z]$/i.test(word);
}

function sentencesOfLine(line: string): string[] {
  const out: string[] = [];
  const re = /[.!?…]+["')\]]*(?=\s+)/g;
  let from = 0;
  for (let m = re.exec(line); m; m = re.exec(line)) {
    const end = m.index + m[0].length;
    const chunk = line.slice(from, end).trim();
    if (m[0].startsWith(".") && m[0].length === 1 && isAbbreviation(line.slice(from, end))) continue;
    if (chunk) out.push(chunk);
    from = end;
  }
  const rest = line.slice(from).trim();
  if (rest) out.push(rest);
  return out;
}

function cutLong(s: string, max: number): string[] {
  const out: string[] = [];
  let rest = s;
  while (rest.length > max) {
    const head = rest.slice(0, max);
    let at = Math.max(head.lastIndexOf(", "), head.lastIndexOf("; "), head.lastIndexOf(": "));
    if (at < max / 3) at = head.lastIndexOf(" ");
    if (at <= 0) at = max - 1;
    out.push(rest.slice(0, at + 1).trim());
    rest = rest.slice(at + 1).trim();
  }
  if (rest) out.push(rest);
  return out;
}

/** Split text into sentence-sized pieces of at most `max` characters. */
export function splitSentences(text: string, max = 300): string[] {
  const raw: string[] = [];
  for (const line of text.split(/\n+/)) {
    for (const s of sentencesOfLine(line)) raw.push(...cutLong(s, max));
  }
  const out: string[] = [];
  let carry = "";
  for (const s of raw) {
    const joined = carry ? `${carry} ${s}` : s;
    if (joined.length < MIN_PIECE) { carry = joined; continue; }
    if (carry && joined.length > max) { out.push(carry); carry = ""; out.push(s); continue; }
    out.push(joined);
    carry = "";
  }
  if (carry) {
    if (out.length && out[out.length - 1].length + carry.length + 1 <= max) out[out.length - 1] += ` ${carry}`;
    else out.push(carry);
  }
  return out;
}

/** What a read of `text` is made of: its words, sentence by sentence. */
export function readPieces(text: string): string[] {
  return splitSentences(speakableText(text));
}
