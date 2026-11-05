// Which model a spoken name means. Matched against what the picker offers,
// by label and by id, with spoken numbers read as digits: "opus five point
// five" is "Opus 5.5". Pure, so it is tested rather than tried by voice.

export type ModelChoice = { id: string; label: string };

const ONES = [
  "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
  "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
  "seventeen", "eighteen", "nineteen",
];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
const POINT = new Set(["point", "dot"]);
// Words said around a name that are not part of it.
const FILLERS = new Set([
  "the", "a", "an", "to", "model", "use", "using", "switch", "set", "change",
  "please", "um", "uh", "er", "version", "and",
]);

function wordValue(w: string): number | null {
  if (/^\d+$/.test(w)) return Number(w);
  const one = ONES.indexOf(w);
  if (one >= 0) return one;
  const ten = TENS.indexOf(w);
  return ten >= 2 ? ten * 10 : null;
}

/** Number words to digits: "five point five" -> "5.5", "twenty one" -> "21".
 *  Everything else is lower-cased and split on anything not a letter, digit
 *  or decimal point. */
export function spokenNumbers(text: string): string {
  const words = tokens(text);
  const out: string[] = [];
  for (let i = 0; i < words.length; i++) {
    let v = wordValue(words[i]);
    if (v === null) { out.push(words[i]); continue; }
    // "twenty one" is one number; "five five" is two.
    if (v >= 20 && v % 10 === 0 && i + 1 < words.length) {
      const next = wordValue(words[i + 1]);
      if (next !== null && next > 0 && next < 10) { v += next; i++; }
    }
    let num = String(v);
    while (i + 2 < words.length && POINT.has(words[i + 1]) && wordValue(words[i + 2]) !== null) {
      num += `.${wordValue(words[i + 2])}`;
      i += 2;
    }
    out.push(num);
  }
  return out.join(" ");
}

/** Lower-cased words and numbers; a full stop ending a word is not part of it. */
function tokens(s: string): string[] {
  return s.toLowerCase().split(/[^a-z0-9.]+/)
    .map((t) => t.replace(/^\.+|\.+$/g, "")).filter(Boolean);
}

type Parsed = { words: string[]; version: string };

function isNum(t: string): boolean { return /^\d+(\.\d+)*$/.test(t); }

/** A query's words, and its numbers read as one version ("5 5" and "5.5"
 *  both mean 5.5). */
function parseQuery(q: string): Parsed {
  const toks = spokenNumbers(q).split(" ").filter(Boolean);
  const words = toks.filter((t) => !isNum(t) && !FILLERS.has(t));
  const version = toks.filter(isNum).join(".");
  return { words, version };
}

/** A model's words from its label and id, and its version: the label's
 *  number when it shows one, else the id's short digit groups (a long group is
 *  a release date, not a version). */
function parseModel(m: ModelChoice): Parsed {
  const split = (s: string) => tokens(
    s.toLowerCase().replace(/([a-z])(\d)/g, "$1 $2").replace(/(\d)([a-z])/g, "$1 $2"));
  const label = split(m.label);
  const id = split(m.id.replace(/\./g, "-"));
  const words = [...new Set([...label, ...id].filter((t) => !isNum(t)))];
  const fromLabel = label.filter(isNum);
  const idNums: string[] = [];
  for (const t of id) {
    if (!isNum(t)) continue;
    if (t.length > 2) break;
    idNums.push(t);
  }
  const version = (fromLabel.length ? fromLabel : idNums).join(".");
  return { words, version };
}

function editDistanceAtMostOne(a: string, b: string): boolean {
  if (a === b) return true;
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
  if (q.length >= 4 && words.some((w) => editDistanceAtMostOne(q, w))) return 1;
  return 0;
}

/** How well `query` names `m`; 0 when it does not. */
export function modelScore(query: string, m: ModelChoice): number {
  const q = parseQuery(query);
  if (!q.words.length && !q.version) return 0;
  const p = parseModel(m);
  let score = 0;
  for (const w of q.words) {
    const s = wordScore(w, p.words);
    if (!s) return 0;
    score += s;
  }
  if (q.version) {
    if (p.version === q.version) score += 4;
    else if (p.version.startsWith(`${q.version}.`)) score += 1;
    else return 0;
  }
  return score;
}

/** The best match, the earlier one on a tie (the picker lists newest first),
 *  or null when nothing matches. */
export function matchModel(query: string, models: ModelChoice[]): ModelChoice | null {
  let best: ModelChoice | null = null;
  let bestScore = 0;
  for (const m of models) {
    const s = modelScore(query, m);
    if (s > bestScore) { best = m; bestScore = s; }
  }
  return best;
}
