// Whether the page look may show markdown source around the caret. Only once
// the person has moved the caret or typed since the document became writable:
// the tap that starts writing changes nothing on screen, so the text stays
// exactly where it was read.
import { StateField, Transaction, type EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

export const revealField = StateField.define<boolean>({
  create: () => false,
  update(v, tr) {
    const writable = tr.state.facet(EditorView.editable);
    if (!writable || writable !== tr.startState.facet(EditorView.editable)) return false;
    if (v) return v;
    return tr.annotation(Transaction.userEvent) !== undefined;
  },
});

/** Whether the selection, while writing, touches from..to. */
export function touched(state: EditorState, from: number, to: number): boolean {
  return state.facet(EditorView.editable) && state.field(revealField)
    && state.selection.ranges.some((r) => r.from <= to && r.to >= from);
}
