// Reaching into app frames. A frame's focus, keys, copies and selection happen
// in the frame's own document and never reach the shell's, so anything the
// shell offers on every page has to listen on each document directly. A frame
// on another origin cannot be reached and is skipped.

/** A frame's document, or null when it is on another origin. */
export function frameDocument(frame: HTMLIFrameElement): Document | null {
  try {
    return frame.contentDocument;
  } catch {
    return null;
  }
}

/** Call `attach` on this document and on every reachable frame document at
 *  any depth, now and whenever a frame loads a new one. Returns a function
 *  that detaches everything. */
export function watchDocuments(attach: (doc: Document) => () => void): () => void {
  const attached = new Map<Document, () => void>();
  const visitFrame = (frame: HTMLIFrameElement) => {
    const doc = frameDocument(frame);
    if (doc) visit(doc);
  };
  const visit = (doc: Document) => {
    for (const [d, off] of attached) {
      if (!d.defaultView) { off(); attached.delete(d); }
    }
    if (attached.has(doc)) return;
    const off = attach(doc);
    const onLoad = (e: Event) => {
      const t = e.target as Element | null;
      if (t && t.tagName === "IFRAME") visitFrame(t as HTMLIFrameElement);
    };
    doc.addEventListener("load", onLoad, true);
    attached.set(doc, () => {
      off();
      doc.removeEventListener("load", onLoad, true);
    });
    doc.querySelectorAll("iframe").forEach(visitFrame);
  };
  visit(document);
  return () => {
    for (const off of attached.values()) {
      try { off(); } catch { /* the document is already gone */ }
    }
    attached.clear();
  };
}
