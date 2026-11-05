// The pure half of the page labels: a control's name from its parts, a spoken
// number, and which labelled target a spoken query means. Kept apart from the
// DOM walk in pageTargets.ts so it can be run in tests.

export type NameParts = {
  labelledBy?: string; ariaLabel?: string; label?: string; placeholder?: string;
  value?: string; alt?: string; text?: string; title?: string;
};

export const NAME_MAX = 40;

const NAME_ORDER: (keyof NameParts)[] = [
  "labelledBy", "ariaLabel", "label", "placeholder", "value", "alt", "text", "title",
];

/** The first part that says something, whitespace collapsed, clipped at a
 *  word boundary when one is near the limit. */
export function nameFrom(parts: NameParts): string {
  for (const key of NAME_ORDER) {
    const text = (parts[key] ?? "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    if (text.length <= NAME_MAX) return text;
    const cut = text.slice(0, NAME_MAX);
    const space = cut.lastIndexOf(" ");
    return (space > NAME_MAX / 2 ? cut.slice(0, space) : cut).trimEnd();
  }
  return "";
}

const ONES = [
  "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

// 1 to 99 in words: "seven", "forty", "forty two".
function belowHundred(words: string[]): number | null {
  if (words.length === 1) {
    const one = ONES.indexOf(words[0]);
    if (one >= 1) return one;
    const ten = TENS.indexOf(words[0]);
    return ten >= 2 ? ten * 10 : null;
  }
  if (words.length === 2) {
    const ten = TENS.indexOf(words[0]);
    const one = ONES.indexOf(words[1]);
    if (ten >= 2 && one >= 1 && one <= 9) return ten * 10 + one;
  }
  return null;
}

/** A number said or typed: "4", "four", "number twelve", "no. 21", "twenty
 *  one", "a hundred and five", "one twenty three" (the way a number past a
 *  hundred is often said). Homophones ("for", "to", "too") are not numbers:
 *  they are far more often words. */
export function spokenNumber(text: string): number | null {
  const words = text.toLowerCase().trim().replace(/^\s*(?:number\b|no\.|no\b|#)\s*/, "")
    .replace(/[.,!?]+$/, "").split(/[\s-]+/).filter((w) => w && w !== "and");
  if (words.length === 1 && /^\d{1,3}$/.test(words[0])) return Number(words[0]);
  if (words.length === 1 && words[0] === "zero") return 0;
  const hundreds = words[0] === "a" ? 1 : ONES.indexOf(words[0]);
  if (words[1] === "hundred" && hundreds >= 1 && hundreds <= 9) {
    if (words.length === 2) return hundreds * 100;
    const rest = belowHundred(words.slice(2));
    return rest === null ? null : hundreds * 100 + rest;
  }
  if (words.length >= 2 && (hundreds === 1 || hundreds === 2)) {
    const rest = belowHundred(words.slice(1));
    if (rest !== null && rest >= 10) return hundreds * 100 + rest;
  }
  return belowHundred(words);
}

const FILLERS = new Set([
  "the", "a", "an", "button", "link", "tab", "field", "icon", "on", "please",
  "to", "of", "for", "my", "me", "it", "this", "that", "and", "i", "you", "with",
]);

/** The words of `text` that can name something. */
export function wordsOf(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w && !FILLERS.has(w));
}

function oneOff(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  let j = 0;
  let edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++;
    else if (b.length > a.length) j++;
    else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

function wordScore(q: string, words: string[]): number {
  if (words.includes(q)) return 3;
  if (q.length >= 3 && words.some((w) => w.startsWith(q))) return 2;
  if (q.length >= 4 && words.some((w) => oneOff(q, w))) return 1;
  return 0;
}

/** How well `query` fits `name`, or null when some query word fits none of
 *  its words: every query word must be one of them, the start of one (3+
 *  letters) or one letter off (4+). `extra` counts name words left over;
 *  `exact` is every query word being one of them. */
export function nameFit(name: string, query: string): { score: number; extra: number; exact: boolean } | null {
  const q = wordsOf(query);
  if (!q.length) return null;
  const words = wordsOf(name);
  let score = 0;
  for (const w of q) {
    const got = wordScore(w, words);
    if (!got) return null;
    score += got;
  }
  return { score, extra: Math.max(0, words.length - q.length), exact: score === 3 * q.length };
}

/** The target `query` means: by number when it is one, else by name
 *  (nameFit). Fewest name words left over wins, then the closer match (so
 *  "send" picks Send over Sent), then the earlier target. */
export function findTarget<T extends { n: number; name: string }>(targets: readonly T[], query: string): T | null {
  const num = spokenNumber(query);
  if (num !== null) return targets.find((t) => t.n === num) ?? null;
  let best: T | null = null;
  let bestExtra = Infinity;
  let bestScore = -Infinity;
  for (const t of targets) {
    const fit = nameFit(t.name, query);
    if (!fit) continue;
    if (fit.extra < bestExtra || (fit.extra === bestExtra && fit.score > bestScore)) {
      best = t;
      bestExtra = fit.extra;
      bestScore = fit.score;
    }
  }
  return best;
}
