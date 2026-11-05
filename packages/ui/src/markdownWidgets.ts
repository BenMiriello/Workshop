// What the page look draws in place of a piece of markdown inside a line: an
// image, and a task's checkbox. Blocks (tables, code) are markdownBlocks.tsx.
import { EditorView, WidgetType } from "@codemirror/view";

/** Only addresses a page may load: the web, or this site. */
const SAFE = /^(https?:|\/(?!\/))/i;

export class ImageWidget extends WidgetType {
  constructor(readonly src: string, readonly alt: string) { super(); }
  eq(o: ImageWidget) { return o.src === this.src && o.alt === this.alt; }
  toDOM(view: EditorView) {
    if (!SAFE.test(this.src)) {
      const span = document.createElement("span");
      span.textContent = this.alt;
      return span;
    }
    const img = document.createElement("img");
    img.className = "cm-md-img";
    img.src = this.src;
    img.alt = this.alt;
    // Its height is known only once it loads; the lines below must move with it.
    img.addEventListener("load", () => view.requestMeasure());
    return img;
  }
  ignoreEvent() { return false; }
}

/** A task's box. Ticking it while writing writes the `x` into the text. */
export class TaskBox extends WidgetType {
  constructor(readonly done: boolean, readonly at: number) { super(); }
  eq(o: TaskBox) { return o.done === this.done && o.at === this.at; }
  toDOM(view: EditorView) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "cm-md-task";
    box.checked = this.done;
    box.addEventListener("mousedown", (e) => e.preventDefault());
    box.addEventListener("click", (e) => {
      if (!view.state.facet(EditorView.editable)) {
        e.preventDefault();
        return;
      }
      view.dispatch({
        changes: { from: this.at + 1, to: this.at + 2, insert: this.done ? " " : "x" },
        userEvent: "input.task",
      });
    });
    return box;
  }
  ignoreEvent() { return true; }
}
