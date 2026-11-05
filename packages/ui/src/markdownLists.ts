// Lists in the editor's page look (markdownPreview.ts), drawn as the reading
// form draws them: each item's bullet or number hangs in the margin, nested
// lists step in by the reading form's indent, and a loose list (blank lines
// between its items) spaces its items a paragraph apart.

import type { EditorState } from "@codemirror/state";
import { Decoration, WidgetType } from "@codemirror/view";
import type { syntaxTree } from "@codemirror/language";

export type SyntaxNode = ReturnType<ReturnType<typeof syntaxTree>["resolveInner"]>;

/** The reading form's marker for the depth: a disc, then a circle, then a square. */
class Bullet extends WidgetType {
  constructor(readonly shape: number) { super(); }
  eq(other: Bullet) { return other.shape === this.shape; }
  toDOM() {
    const el = document.createElement("span");
    el.className = `cm-md-bullet cm-md-bullet--${this.shape}`;
    el.textContent = "•";
    return el;
  }
}
const BULLETS = [1, 2, 3].map((n) => Decoration.replace({ widget: new Bullet(n) }));

/** An ordered item's number, as the reading form counts it. */
class Ordinal extends WidgetType {
  constructor(readonly label: string) { super(); }
  eq(other: Ordinal) { return other.label === this.label; }
  toDOM() {
    const el = document.createElement("span");
    el.className = "cm-md-num";
    const inner = document.createElement("span");
    inner.textContent = this.label;
    el.append(inner);
    return el;
  }
}

/** "3. " for the third item of a list that starts at 1, whatever was typed. */
function ordinal(item: SyntaxNode, state: EditorState): string {
  const items = item.parent?.getChildren("ListItem") ?? [];
  const first = items[0]?.getChild("ListMark");
  const start = first ? Number.parseInt(state.sliceDoc(first.from, first.to), 10) : 1;
  const at = Math.max(0, items.findIndex((i) => i.from === item.from));
  return `${(Number.isNaN(start) ? 1 : start) + at}. `;
}

/** What a list mark is drawn as: a bullet, or its item's number. */
export function listMarker(mark: SyntaxNode, state: EditorState): Decoration {
  const item = mark.parent;
  if (!item || /^[-*+]$/.test(state.sliceDoc(mark.from, mark.to))) {
    return BULLETS[Math.min(item ? listDepth(item) : 1, 3) - 1];
  }
  return Decoration.replace({ widget: new Ordinal(ordinal(item, state)) });
}

const itemLines = new Map<string, Decoration>();
/** A list line at its depth: the item's first line, or one continuing it. */
export function itemLine(depth: number, first: boolean, loose: boolean): Decoration {
  const key = `${depth}:${first}:${loose}`;
  let d = itemLines.get(key);
  if (!d) {
    d = Decoration.line({
      class: `${first ? "cm-md-li" : "cm-md-lic"}${loose ? " cm-md-loose" : ""}`,
      attributes: { style: `padding-left: calc(var(--space-5) * ${depth})` },
    });
    itemLines.set(key, d);
  }
  return d;
}

/** A blank line between the list's first and last written lines. */
export function isLoose(list: SyntaxNode, state: EditorState): boolean {
  const first = state.doc.lineAt(list.from).number;
  let last = state.doc.lineAt(list.to).number;
  while (last > first && !state.doc.line(last).text.trim()) last--;
  for (let n = first + 1; n < last; n++) if (!state.doc.line(n).text.trim()) return true;
  return false;
}

export function listDepth(node: SyntaxNode): number {
  let depth = 0;
  for (let p = node.parent; p; p = p.parent) if (p.name === "BulletList" || p.name === "OrderedList") depth++;
  return depth;
}
