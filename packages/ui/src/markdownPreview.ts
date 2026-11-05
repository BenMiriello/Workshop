// Markdown drawn the way it reads, in the one surface a document is both read
// and written in.
//
// The page look of the editor (`look="page"`). A document is not drawn by a
// reader and then swapped for an editor: it is always this editor, read-only
// while it is being read, so starting to write cannot move anything. Headings
// take their size with their `#` out of sight, lists keep their bullets and
// numbers hanging in the margin, links read as links, images and task boxes
// are drawn in place, and a blank line between paragraphs is the paragraph
// gap. Blocks (tables, code, rules) are markdownBlocks.tsx; a host's own
// additions (wiki links' targets, mentions, words drawn as others) are
// editorMarks.ts, drawn here beside the markdown.
//
// The inline markup (the `**` around bold, a link's `](target)`) comes back
// only around the piece of text the caret has been moved into while writing
// (markdownReveal.ts), so nothing is typed blind and nothing shifts until it is
// being worked on. Headings', quotes' and lists' markers never come back:
// Backspace at their start removes one.
//
// This is decoration over plain source. The text, the undo history and what is
// saved are exactly the markdown; nothing is converted.

import { type Extension, type Range } from "@codemirror/state";
import {
  Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { HighlightStyle, syntaxHighlighting, syntaxTree } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { marksField, mergeMarks, type Painted } from "./editorMarks";
import { markdownBlocks } from "./markdownBlocks";
import { isLoose, itemLine, listDepth, listMarker } from "./markdownLists";
import { revealField, touched } from "./markdownReveal";
import { ImageWidget, TaskBox } from "./markdownWidgets";

const HIDDEN = Decoration.replace({});
const WIKI = /\[\[([^\]|#\n]+)(#[^\]|\n]*)?(\|[^\]\n]*)?\]\]/g;
/** Drawn by markdownBlocks.tsx, or left as written. */
const NOT_INLINE = new Set(["Table", "FencedCode", "CodeBlock", "HorizontalRule", "HTMLBlock"]);

const lineClass = (cls: string) => Decoration.line({ class: cls });
const markClass = (cls: string) => Decoration.mark({ class: cls });
const link = (target: string) => Decoration.mark({ class: "cm-md-link", attributes: { "data-ws-link": target } });
const CODE = markClass("cm-md-code");
const MARK = markClass("cm-md-mark");
const GAP = lineClass("cm-md-gap");
const QUOTES = ["", " cm-md-qtop", " cm-md-qend", " cm-md-qtop cm-md-qend"].map((x) => lineClass(`cm-md-quote${x}`));
const HEADINGS = [1, 2, 3, 4, 5, 6].map((n) => lineClass(`cm-md-h cm-md-h${n}`));

function build(view: EditorView): Painted {
  const { state } = view;
  const near = (from: number, to: number) => touched(state, from, to);
  const out: Range<Decoration>[] = [];
  const sameLine = (from: number, to: number) => state.doc.lineAt(from).number === state.doc.lineAt(to).number;
  // A replaced range from a plugin may not cross a line break.
  const hide = (from: number, to: number) => { if (to > from && sameLine(from, to)) out.push(HIDDEN.range(from, to)); };
  const showOrHide = (around: [number, number], ...parts: [number, number][]) => {
    for (const [a, b] of parts) {
      if (near(...around)) { if (b > a) out.push(MARK.range(a, b)); } else hide(a, b);
    }
  };
  const items = new Map<number, number>();
  const continued = new Map<number, number>();
  const loose = new Map<number, boolean>();
  const looseLists = new Map<number, boolean>();

  for (const { from, to } of view.visibleRanges) {
    // A blank line between two written ones is a paragraph break.
    for (let pos = from; pos <= to;) {
      const line = state.doc.lineAt(pos);
      if (!line.text.trim() && line.number > 1 && line.number < state.doc.lines) out.push(GAP.range(line.from));
      pos = line.to + 1;
    }

    syntaxTree(state).iterate({
      from, to,
      enter: (node) => {
        const name = node.name;
        if (NOT_INLINE.has(name)) return false;
        const heading = /^(?:ATX|Setext)Heading([1-6])$/.exec(name);
        if (heading) { out.push(HEADINGS[Number(heading[1]) - 1].range(state.doc.lineAt(node.from).from)); return; }
        if (name === "Blockquote") {
          const top = state.doc.lineAt(node.from).number;
          const end = state.doc.lineAt(node.to).number;
          for (let n = top; n <= end; n++) {
            out.push(QUOTES[(n === top ? 1 : 0) + (n === end ? 2 : 0)].range(state.doc.line(n).from));
          }
          return;
        }
        if (name === "ListItem") {
          const depth = listDepth(node.node);
          const first = state.doc.lineAt(node.from).number;
          const last = state.doc.lineAt(node.to).number;
          const list = node.node.parent;
          const spaced = list ? (looseLists.get(list.from) ?? isLoose(list, state)) : false;
          if (list) looseLists.set(list.from, spaced);
          items.set(first, depth);
          loose.set(first, spaced);
          for (let n = first + 1; n <= last; n++) {
            if ((continued.get(n) ?? 0) < depth) { continued.set(n, depth); loose.set(n, spaced); }
          }
          return;
        }
        if (name === "InlineCode") { out.push(CODE.range(node.from, node.to)); return; }
        if (name === "Autolink") {
          const url = node.node.getChild("URL");
          if (url) out.push(link(state.sliceDoc(url.from, url.to)).range(url.from, url.to));
          showOrHide([node.from, node.to], [node.from, node.from + 1], [node.to - 1, node.to]);
          return false;
        }
        if (name === "URL") {
          out.push(link(state.sliceDoc(node.from, node.to)).range(node.from, node.to));
          return;
        }
        // A link with a target. A bare `[text]` is not one: it is a reference
        // left as written, or half of a [[wiki link]].
        if ((name === "Link" || name === "Image") && !node.node.getChild("URL")) return false;
        if (name === "Link" || name === "Image") {
          const marks = node.node.getChildren("LinkMark");
          const url = node.node.getChild("URL");
          const open = marks[0];
          const close = marks[1];
          if (!open || !close || !url || close.from < open.to) return false;
          const target = state.sliceDoc(url.from, url.to);
          if (name === "Image" && !near(node.from, node.to) && sameLine(node.from, node.to)) {
            out.push(Decoration.replace({ widget: new ImageWidget(target, state.sliceDoc(open.to, close.from)) })
              .range(node.from, node.to));
            return false;
          }
          out.push(link(target).range(open.to, close.from));
          showOrHide([node.from, node.to], [node.from, open.to], [close.from, node.to]);
          return false;
        }
        if (name === "HeaderMark" || name === "QuoteMark") {
          hide(node.from, node.to + (state.sliceDoc(node.to, node.to + 1) === " " ? 1 : 0));
        } else if (name === "EmphasisMark" || name === "CodeMark" || name === "StrikethroughMark") {
          const around = node.node.parent ?? node.node;
          showOrHide([around.from, around.to], [node.from, node.to]);
        } else if (name === "ListMark") {
          const line = state.doc.lineAt(node.from);
          const start = state.sliceDoc(line.from, node.from).trim() ? node.from : line.from;
          const task = node.node.parent?.getChild("Task")?.getChild("TaskMarker");
          const after = (to: number) => to + (state.sliceDoc(to, to + 1) === " " ? 1 : 0);
          if (task) {
            const done = /x/i.test(state.sliceDoc(task.from, task.to));
            out.push(Decoration.replace({ widget: new TaskBox(done, task.from) }).range(start, after(task.to)));
          } else if (after(node.to) > start) {
            out.push(listMarker(node.node, state).range(start, after(node.to)));
          }
        }
      },
    });

    // [[Wiki links]]: the target (or what it is shown as) reads as a link.
    const text = state.sliceDoc(from, to);
    for (const m of text.matchAll(WIKI)) {
      const start = from + (m.index ?? 0);
      const end = start + m[0].length;
      const [, target, , alias] = m;
      const shownFrom = alias ? end - 2 - alias.length + 1 : start + 2;
      const shownTo = alias ? end - 2 : start + 2 + target.length;
      out.push(link(`wikilink:${target.trim()}`).range(shownFrom, shownTo));
      showOrHide([start, end], [start, shownFrom], [shownTo, end]);
    }
  }

  // A list line sits at its item's indent, its own leading spaces out of sight.
  for (const [n, depth] of continued) {
    if (items.has(n)) continue;
    const line = state.doc.line(n);
    if (!line.text.trim()) continue;
    out.push(itemLine(depth, false, loose.get(n) ?? false).range(line.from));
    hide(line.from, line.from + (line.text.length - line.text.trimStart().length));
  }
  for (const [n, depth] of items) out.push(itemLine(depth, true, loose.get(n) ?? false).range(state.doc.line(n).from));

  return mergeMarks(state, out, view, HIDDEN);
}

const preview = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  atomic: DecorationSet;
  constructor(view: EditorView) { ({ decorations: this.decorations, atomic: this.atomic } = build(view)); }
  update(u: ViewUpdate) {
    if (u.docChanged || u.viewportChanged || u.selectionSet || u.focusChanged
      || u.startState.field(revealField) !== u.state.field(revealField)
      || u.startState.field(marksField) !== u.state.field(marksField)
      || u.startState.facet(EditorView.editable) !== u.state.facet(EditorView.editable)
      || syntaxTree(u.startState) !== syntaxTree(u.state)) {
      ({ decorations: this.decorations, atomic: this.atomic } = build(u.view));
    }
  }
}, {
  decorations: (v) => v.decorations,
  provide: (p) => EditorView.atomicRanges.of((view) => view.plugin(p)?.atomic ?? Decoration.none),
});

/** Emphasis as it reads, without the code editor's colors: a note is not
 *  source, and a colored URL or number reads as a fault in prose. Markup that
 *  is showing is dimmed by the preview (`cm-md-mark`), so brackets and angle
 *  brackets that are only text keep the text's color. */
const PROSE = HighlightStyle.define([
  { tag: t.heading, fontWeight: "var(--fw-semi)" },
  { tag: t.strong, fontWeight: "var(--fw-semi)" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through", color: "var(--muted)" },
  { tag: t.quote, color: "var(--muted)" },
  { tag: t.monospace, fontFamily: "var(--font-mono)" },
]);

/** The page look: prose emphasis, and markdown drawn as it reads. */
export function pageLook(markdownSource: boolean): Extension {
  return [syntaxHighlighting(PROSE), ...(markdownSource ? [revealField, markdownBlocks, preview] : [])];
}
