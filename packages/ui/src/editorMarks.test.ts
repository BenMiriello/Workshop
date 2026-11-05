import { describe, expect, it } from "vitest";
import { EditorState } from "@codemirror/state";
import { marksField, setMarks, type EditorMarks } from "./editorMarks";

const WORD = "fig";

/** Every "fig" drawn as "part", hiding what is written. */
const hideFig: EditorMarks = (text) => [...text.matchAll(/fig/g)].map((x) => ({
  from: x.index ?? 0, to: (x.index ?? 0) + WORD.length, text: "part", hides: true,
}));

function opened(doc: string) {
  const s = EditorState.create({ doc, extensions: [marksField] });
  return s.update({ effects: setMarks.of(hideFig) }).state;
}

const drawn = (s: EditorState) => s.field(marksField).marks.map((m) => s.sliceDoc(m.from, m.to) + "@" + m.from);

describe("hiding marks and what was written", () => {
  it("hides a word that was already there", () => {
    expect(drawn(opened("a fig"))).toEqual(["fig@2"]);
  });

  it("never hides a word typed since the document opened", () => {
    let s = opened("a ");
    for (const ch of "fig ") s = s.update({ changes: { from: s.doc.length, insert: ch }, userEvent: "input.type" }).state;
    expect(s.doc.toString()).toBe("a fig ");
    expect(drawn(s)).toEqual([]);
  });

  it("shows an existing word once it is edited, by typing or deleting in it", () => {
    let s = opened("figs and fig");
    s = s.update({ changes: { from: 3, to: 4 }, userEvent: "delete.backward" }).state;
    expect(s.doc.toString()).toBe("fig and fig");
    expect(drawn(s)).toEqual(["fig@8"]);
  });

  it("keeps hiding an untouched word when typing beside it", () => {
    let s = opened("fig");
    s = s.update({ changes: { from: 3, insert: " ok" }, userEvent: "input.type" }).state;
    expect(drawn(s)).toEqual(["fig@0"]);
  });

  it("treats text arriving from elsewhere as not written here", () => {
    let s = opened("a");
    s = s.update({ changes: { from: 1, insert: " fig" } }).state;
    expect(drawn(s)).toEqual(["fig@2"]);
  });
});
