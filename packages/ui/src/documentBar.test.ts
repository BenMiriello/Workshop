import { describe, it, expect } from "vitest";
import { documentBar, type DocumentBarInput } from "./documentBar";

// The header's state table, executed. The chrome is measured in
// docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 6; the two
// axes it serves are file-viewer-and-editor-2026-08-22.md section 4.

const source = (over: Partial<DocumentBarInput> = {}): DocumentBarInput => ({
  writable: true,
  hasRenderedForm: false,
  form: "source",
  dirty: false,
  ...over,
});

const markdown = (over: Partial<DocumentBarInput> = {}): DocumentBarInput => ({
  writable: true,
  hasRenderedForm: true,
  form: "rendered",
  dirty: false,
  ...over,
});

describe("a source file has one form", () => {
  it("offers no view/edit toggle", () => {
    // Not a special case: there is no rendered main.py, so there is no second
    // form to switch to. file-viewer section 4.
    expect(documentBar(source()).toggle).toBeNull();
    expect(documentBar(source({ dirty: true })).toggle).toBeNull();
  });

  it("always draws Save, and enables it once there are changes", () => {
    expect(documentBar(source())).toMatchObject({ save: true, saveEnabled: false });
    expect(documentBar(source({ dirty: true }))).toMatchObject({ save: true, saveEnabled: true });
  });
});

describe("the dot means dirty, and only dirty", () => {
  it("is off on a file nobody has touched", () => {
    // The mockup draws it unconditionally because its editor has exactly one
    // state. Here dirty is knowable, so the dot answers it.
    expect(documentBar(source()).dot).toBe(false);
    expect(documentBar(markdown()).dot).toBe(false);
  });

  it("is on whenever there are unsaved changes, in either form", () => {
    expect(documentBar(source({ dirty: true })).dot).toBe(true);
    expect(documentBar(markdown({ dirty: true })).dot).toBe(true);
    expect(documentBar(markdown({ form: "source", dirty: true })).dot).toBe(true);
  });
});

describe("a file you may not write gets no controls at all", () => {
  it("draws no Save, no Discard and no toggle", () => {
    expect(documentBar(source({ writable: false }))).toEqual({
      dot: false, toggle: null, save: false, saveEnabled: false, discard: false,
    });
  });

  it("stays bare even for markdown, which would otherwise toggle", () => {
    // Read-only is not "view mode". Offering Edit on a path that refuses the
    // write is the dead control design rule 10 is about.
    expect(documentBar(markdown({ writable: false })).toggle).toBeNull();
    expect(documentBar(markdown({ writable: false })).save).toBe(false);
  });
});

describe("markdown carries both forms", () => {
  it("offers Edit while rendered and View while showing the source", () => {
    expect(documentBar(markdown()).toggle).toEqual({ to: "source", label: "Edit" });
    expect(documentBar(markdown({ form: "source" })).toggle).toEqual({ to: "rendered", label: "View" });
  });

  it("hides Save on a clean rendered document, and shows it in the source form", () => {
    expect(documentBar(markdown()).save).toBe(false);
    expect(documentBar(markdown({ form: "source" })).save).toBe(true);
  });

  it("keeps Save reachable from the rendered view of a dirty document", () => {
    // Switching form does not discard the draft, so the rendered view of a
    // dirty document is a preview of what you wrote. If Save vanished there,
    // the only way to commit it would be to switch back.
    expect(documentBar(markdown({ dirty: true }))).toMatchObject({
      save: true, saveEnabled: true, discard: true,
    });
  });
});

describe("Discard", () => {
  it("appears only when there is something to discard", () => {
    expect(documentBar(source()).discard).toBe(false);
    expect(documentBar(source({ dirty: true })).discard).toBe(true);
  });
});
