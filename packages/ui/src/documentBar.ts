// WHAT THE DOCUMENT HEADER SHOWS, as a pure function.
//
// The chrome around the file viewer/editor is measured in
// docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 6, and the two
// axes it has to serve are decided in
// docs/platform-redesign/file-viewer-and-editor-2026-08-22.md section 4:
//
//   form      rendered | source   the reader picks, and ONLY for a file whose
//                                 rendered form differs from its source
//   writable  true | false        the backend's answer about the PATH
//
// The mockup cannot express either of them: its editor is a placeholder with
// exactly one state, "editing, with unsaved changes". So it draws its amber dot
// unconditionally and has no toggle and no read-only case. None of that is a
// decision to copy; the geometry is, and the states are worked out here.
//
// This is the whole rule, and it is a value rather than a tree of `&&` inside
// two components, because there is no DOM harness in this repo: a pure function
// is the part of a surface that can actually be tested.

export type DocumentForm = "rendered" | "source";

export interface DocumentBarInput {
  /** May this path be written. The backend says so (`FileContent.writable`
   *  AND `editable`); it is never inferred from a path here. */
  writable: boolean;
  /** Does this file HAVE a rendered form that differs from its source. True
   *  for markdown, false for source code, which is why source code gets no
   *  view/edit toggle and that is not a special case. */
  hasRenderedForm: boolean;
  /** Which form is on screen right now. */
  form: DocumentForm;
  /** Are there unsaved changes. A real boolean from `useEditMode`. */
  dirty: boolean;
}

export interface DocumentBarModel {
  /** The unsaved dot. It is the ONLY thing in this app that says "unsaved" on
   *  this surface: the sticky ActionBar's "unsaved" pill goes with the
   *  ActionBar, since the header carries Save here. */
  dot: boolean;
  /** The form toggle, or null when the file has only one form. `to` is the
   *  form it switches TO, which is also what its label names. */
  toggle: { to: DocumentForm; label: string } | null;
  /** Is Save drawn at all. */
  save: boolean;
  /** Is it pressable. Drawn-and-disabled rather than hidden, because a Save
   *  that appears the moment you type moves every control beside it. */
  saveEnabled: boolean;
  /** Is Discard drawn. Desktop only, and the component hides it below 900px:
   *  the mobile header has no Discard (mockup spec section 6). */
  discard: boolean;
}

/**
 * Everything the header draws, from four booleans.
 *
 * The cases, so the table below is readable as one:
 *
 *   source file, writable      Save always drawn, enabled once dirty. No
 *                              toggle: `main.py` has one form.
 *   source file, read only     nothing. A file you may not write is not a
 *                              broken text box, so it gets no dead controls
 *                              (and never the `not-allowed` cursor, design
 *                              rule 10).
 *   markdown, rendered, clean  Edit, and nothing else.
 *   markdown, source           View, plus Save.
 *   markdown, rendered, dirty  Edit, plus Save, plus the dot. Switching form
 *                              does NOT throw the draft away, so the rendered
 *                              view of a dirty document is a preview of what
 *                              you wrote, and Save stays reachable from it.
 */
export function documentBar(input: DocumentBarInput): DocumentBarModel {
  const { writable, hasRenderedForm, form, dirty } = input;
  const editable = writable;
  return {
    dot: dirty,
    toggle:
      hasRenderedForm && editable
        ? form === "rendered"
          ? { to: "source", label: "Edit" }
          : { to: "rendered", label: "View" }
        : null,
    // In the source form Save is part of the furniture; in the rendered form it
    // exists only because a draft does.
    save: editable && (form === "source" || dirty),
    saveEnabled: dirty,
    discard: editable && dirty,
  };
}
