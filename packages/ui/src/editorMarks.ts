// What a host adds to how a document is drawn: a range shown as other text, a
// range given a class, a range that is a link. Plain data, like editorAssist
// and editorConceal, so an app bundle can pass it. The page look
// (markdownPreview.ts) draws these alongside its own markdown, in reading and
// in writing alike, so a host's additions never differ between the two.
import { RangeSetBuilder, StateEffect, StateField, type EditorState, type Range, type Transaction } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, type DecorationSet } from "@codemirror/view";
import { touched } from "./markdownReveal";

export interface EditorMark {
  from: number;
  to: number;
  /** Drawn in place of the range; "" draws nothing there. */
  text?: string;
  /** A class for the range, or for the text drawn in its place. */
  className?: string;
  /** Activating the range hands this to the editor's `onLink`. */
  link?: string;
  /** The mark hides what is written there. It is never drawn over text
   *  written or edited in this editor since the document opened: a word
   *  somebody has just typed reads as they typed it. */
  hides?: boolean;
}

/** The marks for a document's text, as offsets into it. */
export type EditorMarks = (text: string) => EditorMark[];

export const setMarks = StateEffect.define<EditorMarks | null>();

type Span = [number, number];

interface MarkState { fn: EditorMarks | null; marks: EditorMark[]; written: Span[] }

const WRITES = ["input", "delete", "move", "undo", "redo"];

/** Where the person has written since the document opened, kept through
 *  every later change; a deletion leaves an empty span where it was. */
function writtenAfter(prev: Span[], tr: Transaction): Span[] {
  if (!tr.docChanged) return prev;
  const out: Span[] = prev.map(([a, b]) => [tr.changes.mapPos(a, -1), tr.changes.mapPos(b, 1)]);
  if (WRITES.some((e) => tr.isUserEvent(e))) tr.changes.iterChangedRanges((_a, _b, from, to) => { out.push([from, to]); });
  out.sort((x, y) => x[0] - y[0]);
  const merged: Span[] = [];
  for (const s of out) {
    const last = merged[merged.length - 1];
    if (last && s[0] <= last[1]) last[1] = Math.max(last[1], s[1]);
    else merged.push([s[0], s[1]]);
  }
  return merged;
}

const overWritten = (m: EditorMark, written: Span[]) => written.some(([a, b]) =>
  (a === b ? m.from <= a && a <= m.to : m.from < b && m.to > a));

const compute = (fn: EditorMarks | null, state: EditorState, written: Span[]): EditorMark[] => {
  if (!fn) return [];
  const len = state.doc.length;
  return fn(state.doc.toString())
    .map((m) => ({ ...m, from: Math.max(0, Math.min(m.from, len)), to: Math.max(0, Math.min(m.to, len)) }))
    .filter((m) => m.to > m.from && !(m.hides && overWritten(m, written)))
    .sort((a, b) => a.from - b.from);
};

/** The host's marks, worked out again whenever the text or the host's rule changes. */
export const marksField = StateField.define<MarkState>({
  create: () => ({ fn: null, marks: [], written: [] }),
  update(v, tr) {
    let fn = v.fn;
    for (const e of tr.effects) if (e.is(setMarks)) fn = e.value;
    if (fn === v.fn && !tr.docChanged) return v;
    const written = writtenAfter(v.written, tr);
    return { fn, marks: compute(fn, tr.state, written), written };
  },
});

/** A host mark's replacement text. */
export class MarkText extends WidgetType {
  constructor(readonly text: string, readonly className: string, readonly link: string) { super(); }
  eq(o: MarkText) { return o.text === this.text && o.className === this.className && o.link === this.link; }
  toDOM() {
    const el = document.createElement("span");
    el.textContent = this.text;
    if (this.className) el.className = this.className;
    if (this.link) el.dataset.wsLink = this.link;
    return el;
  }
  ignoreEvent() { return false; }
}

/** Following a link: a tap while reading, Ctrl or Cmd with a click while
 *  writing, as in every editor where a click must also place the caret. */
export function linkHandler(onLink: () => ((target: string) => void) | undefined) {
  return EditorView.domEventHandlers({
    click(e, view) {
      const el = (e.target as HTMLElement | null)?.closest<HTMLElement>("[data-ws-link]");
      const target = el?.dataset.wsLink;
      const cb = onLink();
      if (!target || !cb) return false;
      if (view.state.facet(EditorView.editable) && !(e.metaKey || e.ctrlKey)) return false;
      e.preventDefault();
      cb(target);
      return true;
    },
  });
}

export interface Painted { decorations: DecorationSet; atomic: DecorationSet }

/** The host's marks over the markdown's, and the two merged into one set. A
 *  host's replacement wins over what the markdown draws in a range (a bullet,
 *  an image); markup the markdown hides (a link's target) stays hidden, and a
 *  host mark over it is not drawn. Like markup, a replacement shows what is
 *  written under it only where the caret has been moved to write, so nothing
 *  else moves. */
export function mergeMarks(state: EditorState, out: Range<Decoration>[], view: EditorView,
  hidden: Decoration): Painted {
  const hosted: [number, number][] = [];
  const shown = view.visibleRanges;
  const unseen = out.filter((r) => r.value === hidden);
  for (const m of state.field(marksField).marks) {
    if (!shown.some((r) => m.to >= r.from && m.from <= r.to)) continue;
    if (unseen.some((r) => m.from < r.to && m.to > r.from)) continue;
    if (m.text !== undefined) {
      if (touched(state, m.from, m.to)) continue;
      if (state.doc.lineAt(m.from).number !== state.doc.lineAt(m.to).number) continue;
      out.push(Decoration.replace({ widget: new MarkText(m.text, m.className ?? "", m.link ?? "") }).range(m.from, m.to));
      hosted.push([m.from, m.to]);
    } else {
      const attributes = m.link ? { "data-ws-link": m.link } : undefined;
      out.push(Decoration.mark({ class: m.className ?? "", attributes }).range(m.from, m.to));
    }
  }
  const isHosted = (r: Range<Decoration>) => r.value.spec.widget instanceof MarkText;
  const underHost = (r: Range<Decoration>) => hosted.some(([a, b]) => r.from < b && r.to > a);

  out.sort((a, b) => a.from - b.from || a.value.startSide - b.value.startSide);
  const all = new RangeSetBuilder<Decoration>();
  const atomic = new RangeSetBuilder<Decoration>();
  let lastHidden = -1;
  for (const r of out) {
    const replaces = r.value.spec.widget !== undefined || r.value === hidden;
    if (replaces) {
      if (!isHosted(r) && underHost(r)) continue;
      // Two hidden ranges must not overlap; the first one wins.
      if (r.from < lastHidden) continue;
      lastHidden = r.to;
      atomic.add(r.from, r.to, r.value);
    }
    all.add(r.from, r.to, r.value);
  }
  return { decorations: all.finish(), atomic: atomic.finish() };
}
