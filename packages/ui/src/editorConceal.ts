// Ranges of the text drawn blurred while editing, except near the caret, so a
// host can keep what is written out of sight while it is being written. The
// host says which ranges; the editor keeps them current as the text and the
// caret move. Plain data, like editorAssist, so an app bundle can pass it.
import { StateEffect, StateField, type Extension, type Range } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from "@codemirror/view";

export interface EditorConceal {
  /** The ranges to blur in `text`, as document offsets. */
  ranges: (text: string) => { from: number; to: number }[];
  /** Characters on each side of the caret drawn as typed. */
  reveal: number;
}

export const setConceal = StateEffect.define<EditorConceal | null>();

const concealConfig = StateField.define<EditorConceal | null>({
  create: () => null,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setConceal)) return e.value;
    return v;
  },
});

const blurred = Decoration.mark({ class: "ws-editor__concealed" });

class ConcealView {
  decorations: DecorationSet = Decoration.none;
  private found: { from: number; to: number }[] = [];
  private conf: EditorConceal | null = null;

  constructor(view: EditorView) {
    this.measure(view, true);
  }

  update(u: ViewUpdate) {
    const conf = u.state.field(concealConfig);
    const fresh = conf !== this.conf || u.docChanged;
    if (fresh || u.selectionSet) this.measure(u.view, fresh);
  }

  private measure(view: EditorView, fresh: boolean) {
    const conf = view.state.field(concealConfig);
    this.conf = conf;
    if (!conf) { this.found = []; this.decorations = Decoration.none; return; }
    if (fresh) this.found = conf.ranges(view.state.doc.toString());
    const head = view.state.selection.main.head;
    const lo = head - conf.reveal;
    const hi = head + conf.reveal;
    const marks: Range<Decoration>[] = [];
    const len = view.state.doc.length;
    for (const r of this.found) {
      const from = Math.max(0, Math.min(r.from, len));
      const to = Math.max(from, Math.min(r.to, len));
      if (to <= lo || from >= hi) { if (to > from) marks.push(blurred.range(from, to)); continue; }
      if (from < lo) marks.push(blurred.range(from, lo));
      if (to > hi) marks.push(blurred.range(hi, to));
    }
    this.decorations = Decoration.set(marks, true);
  }
}

export const concealExtension: Extension = [
  concealConfig,
  ViewPlugin.fromClass(ConcealView, { decorations: (v) => v.decorations }),
];
