// What a HOST adds to the Editor without handing it CodeMirror objects.
//
// An app bundle cannot pass extensions in: the shell owns the one copy of
// @codemirror/state, and an extension built against a second copy is rejected
// at runtime ("Unrecognized extension value"). So the Editor takes plain data
// and builds the extensions here, inside the kit, against the kit's own copy:
//
//   completions   a trigger ("@", "[[") and a function from the typed query to
//                 suggestions. What a suggestion inserts is the host's call.
//   highlights    ranges to mark, each with a tone and a tooltip. They follow
//                 the text as it is edited, until the host sends new ones.
//   selection     the selected range and its text, or null when it collapses.
//
// The matching is pure and lives in `matchTrigger` so it is tested without a DOM.
import { StateEffect, StateField, type Extension, type Range } from "@codemirror/state";
import { Decoration, EditorView, type DecorationSet } from "@codemirror/view";
import {
  autocompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";

export interface EditorCompletionItem {
  /** What the list shows, and what is inserted unless `insert` says otherwise. */
  label: string;
  /** One muted line beside the label: a kind, a count, a confidence. */
  detail?: string;
  /** The exact text that replaces the trigger and the query. Defaults to
   *  trigger + label + close. */
  insert?: string;
}

export interface EditorCompletionSource {
  /** Typed directly before the query, e.g. "@" or "[[". */
  trigger: string;
  /** Closes the inserted text, e.g. "]]". A source with a close accepts spaces
   *  in its query, because the close is what ends it; one without stops at the
   *  first space. */
  close?: string;
  options: (query: string) => EditorCompletionItem[] | Promise<EditorCompletionItem[]>;
}

export type EditorHighlightTone = "accent" | "ok" | "warn" | "info" | "muted";

export interface EditorHighlight {
  from: number;
  to: number;
  tone?: EditorHighlightTone;
  /** Shown on hover. */
  title?: string;
}

export interface EditorSelection {
  from: number;
  to: number;
  text: string;
}

/** Where a trigger's query starts in `before` (the text from the start of the
 *  line to the cursor), and the query itself, or null when the cursor is not in
 *  one. The LAST occurrence of the trigger wins, and a query never spans a
 *  close or, for a source without one, a space. */
export function matchTrigger(
  before: string,
  trigger: string,
  close?: string,
): { start: number; query: string } | null {
  const at = before.lastIndexOf(trigger);
  if (at < 0) return null;
  // "@" inside a word is an address, not a mention: "me@site" is not a trigger.
  if (at > 0 && trigger.startsWith("@") && /[\w.]/.test(before[at - 1])) return null;
  const query = before.slice(at + trigger.length);
  if (close && query.includes(close)) return null;
  if (!close && /\s/.test(query)) return null;
  if (query.includes("\n")) return null;
  return { start: at, query };
}

function completionSource(sources: () => EditorCompletionSource[]) {
  return async (ctx: CompletionContext): Promise<CompletionResult | null> => {
    const line = ctx.state.doc.lineAt(ctx.pos);
    const before = line.text.slice(0, ctx.pos - line.from);
    for (const src of sources()) {
      const m = matchTrigger(before, src.trigger, src.close);
      if (!m) continue;
      const items = await src.options(m.query);
      if (ctx.aborted) return null;
      const from = line.from + m.start;
      // Swallow a close the writer already typed, so accepting inside "[[|]]"
      // does not leave "]]]]".
      const after = line.text.slice(ctx.pos - line.from);
      const trailing = src.close && after.startsWith(src.close) ? src.close.length : 0;
      const options: Completion[] = items.map((it) => ({
        label: it.label,
        detail: it.detail,
        apply: (view, _c, f, t) => {
          const text = it.insert ?? `${src.trigger}${it.label}${src.close ?? ""}`;
          view.dispatch({
            changes: { from: f, to: t + trailing, insert: text },
            selection: { anchor: f + text.length },
          });
        },
      }));
      return { from, to: ctx.pos, options, filter: false };
    }
    return null;
  };
}

/** Autocompletion over the host's sources, read through a getter so a new
 *  sources array never needs a reconfigure. */
export function completionExtension(sources: () => EditorCompletionSource[]): Extension {
  return autocompletion({
    override: [completionSource(sources)],
    activateOnTyping: true,
    icons: false,
  });
}

export const setHighlights = StateEffect.define<EditorHighlight[]>();

function build(ranges: EditorHighlight[], length: number): DecorationSet {
  const marks: Range<Decoration>[] = [];
  for (const h of ranges) {
    const from = Math.max(0, Math.min(h.from, length));
    const to = Math.max(from, Math.min(h.to, length));
    if (to <= from) continue;
    marks.push(
      Decoration.mark({
        class: `ws-editor__hl ws-editor__hl--${h.tone ?? "accent"}`,
        attributes: h.title ? { title: h.title } : undefined,
      }).range(from, to),
    );
  }
  return Decoration.set(marks, true);
}

/** Highlights as state: mapped through every edit, replaced wholesale by
 *  `setHighlights`. */
export const highlightField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(set, tr) {
    let next = set.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setHighlights)) next = build(e.value, tr.state.doc.length);
    }
    return next;
  },
  provide: (f) => EditorView.decorations.from(f),
});

export function selectionListener(report: () => ((sel: EditorSelection | null) => void) | undefined): Extension {
  return EditorView.updateListener.of((u) => {
    if (!u.selectionSet) return;
    const cb = report();
    if (!cb) return;
    const r = u.state.selection.main;
    cb(r.empty ? null : { from: r.from, to: r.to, text: u.state.sliceDoc(r.from, r.to) });
  });
}
