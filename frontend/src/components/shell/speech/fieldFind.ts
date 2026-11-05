// Finding a field again after it left the page: every open document (the
// shell's and each reachable app frame's) is kept here, and a field is looked
// up in them by its identity (lib/speech/fieldIdentity.ts).

import { sameIdentity, stableKey, type FieldIdentity } from "../../../lib/speech/fieldIdentity";
import { dictationKindOf } from "../../../lib/speech/targets";
import { documentShown, watchDocuments } from "./frames";

const FIELDS = "input, textarea, [contenteditable]:not([contenteditable='false']), [role='textbox']";

const docs = new Set<Document>();
let watching = false;

function openDocuments(): Document[] {
  if (!watching && typeof document !== "undefined") {
    watching = true;
    watchDocuments((doc) => {
      docs.add(doc);
      return () => { docs.delete(doc); };
    });
  }
  return [...docs].filter((d) => d.defaultView && documentShown(d));
}

function keyOf(el: Element): string {
  return stableKey({
    id: el.id,
    name: el.getAttribute("name"),
    ariaLabel: el.getAttribute("aria-label"),
    placeholder: el.getAttribute("placeholder") ?? el.getAttribute("data-placeholder"),
  });
}

function sameKeyed(doc: Document, key: string): Element[] {
  return [...doc.querySelectorAll(FIELDS)].filter((f) => dictationKindOf(f) && keyOf(f) === key);
}

export function identityOf(el: Element): FieldIdentity | null {
  const key = keyOf(el);
  if (!key) return null;
  const index = sameKeyed(el.ownerDocument, key).indexOf(el);
  return index < 0 ? null : { path: el.ownerDocument.location.pathname, key, index };
}

/** The field with this identity on an open page now, if there is one. */
export function findField(id: FieldIdentity): HTMLElement | null {
  for (const doc of openDocuments()) {
    if (doc.location.pathname !== id.path) continue;
    const el = sameKeyed(doc, id.key)[id.index];
    if (el && sameIdentity(identityOf(el), id)) return el as HTMLElement;
  }
  return null;
}
