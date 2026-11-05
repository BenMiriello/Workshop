// The blocks of a document that are not lines of text: tables, code blocks
// (and the diagrams drawn from them) and rules. The page look draws each one
// with the kit's one markdown renderer, in place, in reading and in writing
// alike, so they look the same however the document is open. A block turns to
// its source only while the caret has been moved into it to write.
//
// Block widgets have to come from a state field: CodeMirror does not let a
// view plugin replace a range that spans lines.
import { StateField, type EditorState, type Extension, type Range } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, type DecorationSet } from "@codemirror/view";
import { syntaxTree } from "@codemirror/language";
import { createRoot, type Root } from "react-dom/client";
import { Markdown } from "./Markdown";
import { marksField, type EditorMark } from "./editorMarks";
import { revealField } from "./markdownReveal";

const BLOCKS = new Set(["Table", "FencedCode", "CodeBlock", "HorizontalRule"]);

/** The block's markdown with the host's replacement text written in. */
function withMarks(source: string, from: number, marks: EditorMark[]): string {
  let out = "";
  let at = 0;
  for (const m of marks) {
    if (m.text === undefined || m.from - from < at) continue;
    out += source.slice(at, m.from - from) + m.text;
    at = m.to - from;
  }
  return out + source.slice(at);
}

/** A host's class on words inside a drawn block, found where they are drawn. */
function classWords(root: HTMLElement, words: { text: string; className: string }[]) {
  if (!words.length) return;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let i = 0;
  for (let n = walk.nextNode() as Text | null; n && i < words.length;) {
    const at = n.data.indexOf(words[i].text);
    if (at < 0) { n = walk.nextNode() as Text | null; continue; }
    const hit = n.splitText(at);
    const rest = hit.splitText(words[i].text.length);
    const span = document.createElement("span");
    span.className = words[i].className;
    hit.replaceWith(span);
    span.append(hit);
    n = rest;
    i++;
  }
}

class Block extends WidgetType {
  private root: Root | null = null;
  private watch: ResizeObserver | null = null;
  constructor(readonly source: string, readonly words: { text: string; className: string }[]) { super(); }
  eq(o: Block) {
    return o.source === this.source && JSON.stringify(o.words) === JSON.stringify(this.words);
  }
  toDOM(view: EditorView) {
    const el = document.createElement("div");
    el.className = "cm-md-block";
    this.root = createRoot(el);
    this.root.render(<Markdown>{this.source}</Markdown>);
    // Drawn after this returns, and a diagram later still: the editor is told
    // the height changed, or the lines below it sit at the old height.
    this.watch = new ResizeObserver(() => {
      classWords(el, this.words);
      view.requestMeasure();
    });
    this.watch.observe(el);
    return el;
  }
  destroy() {
    this.watch?.disconnect();
    const root = this.root;
    this.root = null;
    if (root) queueMicrotask(() => root.unmount());
  }
  ignoreEvent() { return false; }
}

function build(state: EditorState): DecorationSet {
  const out: Range<Decoration>[] = [];
  const marks = state.field(marksField).marks;
  const writing = state.facet(EditorView.editable) && state.field(revealField);
  syntaxTree(state).iterate({
    enter: (node) => {
      if (!BLOCKS.has(node.name)) return node.name === "Document" || node.name === "Blockquote"
        || node.name === "BulletList" || node.name === "OrderedList" || node.name === "ListItem";
      const from = state.doc.lineAt(node.from).from;
      const to = node.to;
      if (writing && state.selection.ranges.some((r) => r.from <= to && r.to >= from)) return false;
      const inside = marks.filter((m) => m.from >= from && m.to <= to);
      const source = withMarks(state.sliceDoc(from, to), from, inside);
      const words = inside.filter((m) => m.text === undefined && m.className)
        .map((m) => ({ text: state.sliceDoc(m.from, m.to), className: m.className ?? "" }));
      out.push(Decoration.replace({ widget: new Block(source, words), block: true }).range(from, to));
      return false;
    },
  });
  return Decoration.set(out, true);
}

const blocks = StateField.define<DecorationSet>({
  create: (state) => build(state),
  update(set, tr) {
    const changed = tr.docChanged || tr.selection || tr.reconfigured
      || tr.startState.field(revealField) !== tr.state.field(revealField)
      || tr.startState.field(marksField) !== tr.state.field(marksField)
      || syntaxTree(tr.startState) !== syntaxTree(tr.state);
    return changed ? build(tr.state) : set;
  },
  provide: (f) => EditorView.decorations.from(f),
});

export const markdownBlocks: Extension = blocks;
