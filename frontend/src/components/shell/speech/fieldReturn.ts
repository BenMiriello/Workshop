// When a field words go to left the page and came back (dictationWords.ts):
// what waited for it goes in only once the operator has pressed in it. A page
// can focus a field itself as it opens, and an editor still loading writes its
// content over anything put in it. An editor that came back as a new element
// takes nothing until then either, the words still being said included, nor
// does one that was away (its page moved on to another note and back):
// written into while the operator is not in it, an editor may drop them.
// Until then words wait, shown on the bar.

import { focused, insertAtTarget, type DictationTarget } from "./insertAtTarget";
import type { Sink } from "./dictationWords";

export class FieldReturn {
  private pressedIn: Node | null = null;
  private wroteIn: Element | null = null;
  /** The editor words went into left the page (or its page moved on) since. */
  private away = false;

  /** Where words go now: `sink`, or waiting while the field is not taken
   *  back. `waiting`: words already wait for it. */
  route(sink: Sink, waiting: boolean): Sink {
    if (sink.kind !== "live") {
      if (sink.kind === "wait" && this.wroteIn && !this.wroteIn.matches("input, textarea")) this.away = true;
      return sink;
    }
    const el = sink.target.el;
    const back = !!this.pressedIn && el.contains(this.pressedIn) && focused(el);
    const returned = sink.target.kind !== "value" && (this.away || (!!this.wroteIn && this.wroteIn !== el));
    if ((waiting || returned) && !back) return { kind: "wait", name: sink.name };
    this.wroteIn = el;
    this.away = false;
    return sink;
  }

  pressed(target: Node | null): void {
    this.pressedIn = target;
  }

  /** `text` in at the end of the field, as typing would. */
  putIn(t: DictationTarget, text: string): void {
    this.pressedIn = null;
    if (t.kind === "value") {
      const f = t.el as HTMLInputElement | HTMLTextAreaElement;
      try { f.setSelectionRange(f.value.length, f.value.length); } catch { /* no selection API */ }
    } else {
      t.range = null;
      const sel = t.el.ownerDocument.getSelection();
      sel?.selectAllChildren(t.el);
      sel?.collapseToEnd();
    }
    insertAtTarget(t, text);
  }

  reset(): void {
    this.pressedIn = null;
    this.wroteIn = null;
    this.away = false;
  }
}
