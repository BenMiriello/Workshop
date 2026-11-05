// Copies made from code. A copy button writes through `navigator.clipboard`,
// which fires no DOM `copy` event, so the relay wraps that clipboard's two
// write methods on every document it watches and reports each write that
// lands. Apps need nothing of their own for their copy buttons to be seen.
// While copies stay in the Workshop (`hold`), a write is reported and never
// reaches the system clipboard, and a keyboard or menu copy is cancelled the
// same way (`holdCopyEvent`).

let quiet = 0;

/** Run `write` (which starts a clipboard write synchronously) without that
 *  write being reported as a copy: the shell's own dictation into the
 *  clipboard is reported as a dictation instead. */
export function unreported<T>(write: () => T): T {
  quiet += 1;
  try {
    return write();
  } finally {
    quiet -= 1;
  }
}

type Item = { readonly types: readonly string[]; getType(type: string): Promise<Blob> };

/** The plain text a `write` call put on the clipboard, or null for one that
 *  carried none (an image alone). */
export async function itemsText(items: readonly Item[]): Promise<string | null> {
  const item = items.find((i) => i.types.includes("text/plain"));
  return item ? (await item.getType("text/plain")).text() : null;
}

type Writes = Pick<Clipboard, "writeText"> & Partial<Pick<Clipboard, "write">>;

/** Wrap `clip`'s writes so each one that lands calls `report` with its text.
 *  When `hold()` answers true at the time of a write, the write is reported
 *  and succeeds without touching the system clipboard. Returns the function
 *  that puts them back. */
export function wrapWrites(
  clip: Writes, report: (text: string) => void, hold: () => boolean = () => false,
): () => void {
  const { writeText, write } = clip;
  const had = {
    writeText: Object.getOwnPropertyDescriptor(clip, "writeText"),
    write: Object.getOwnPropertyDescriptor(clip, "write"),
  };
  const set = (name: keyof typeof had, value: unknown) =>
    Object.defineProperty(clip, name, { value, configurable: true, writable: true });

  set("writeText", function (this: Writes, text: string) {
    const loud = quiet === 0;
    const done = hold() ? Promise.resolve() : writeText.call(this, text);
    if (loud) done.then(() => report(String(text)), () => {});
    return done;
  });
  if (typeof write === "function") {
    set("write", function (this: Writes, items: ClipboardItems) {
      const loud = quiet === 0;
      const done = hold() ? Promise.resolve() : write.call(this, items);
      if (loud) {
        done.then(() => itemsText(items)).then((t) => { if (t != null) report(t); }, () => {});
      }
      return done;
    });
  }
  return () => {
    for (const name of ["writeText", "write"] as const) {
      const before = had[name];
      if (before) Object.defineProperty(clip, name, before);
      else delete (clip as Partial<Writes>)[name];
    }
  };
}

/** Keep a keyboard or menu copy off the system clipboard. The copy is
 *  cancelled, and what a page's own copy handler puts on the event is handed
 *  to `took` instead of being written: a cancelled copy event with nothing
 *  placed on it leaves the system clipboard as it was. Call it from the
 *  capture phase, before any page handler. */
export function holdCopyEvent(e: ClipboardEvent, took: (text: string) => void): void {
  e.preventDefault();
  const data = e.clipboardData;
  if (!data) return;
  const own = (value: unknown) => ({ value, configurable: true, writable: true });
  Object.defineProperty(data, "setData", own((type: string, value: string) => {
    if (type === "text/plain" || type === "text") took(String(value));
  }));
  Object.defineProperty(data, "clearData", own(() => {}));
}
