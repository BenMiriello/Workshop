// What to call the place dictated words are going: the field's own name and
// the page it is on ("Message in Chats"). Read from what every field and page
// already has, so no app declares anything for it.
//
// The page name is published by the shell (the place and page it is showing)
// and read here at the moment a field becomes the destination, so leaving the
// page afterwards does not rename where the words went.

const MAX_FIELD = 32;

export type FieldNaming = {
  ariaLabel?: string | null;
  labelledBy?: string | null;
  label?: string | null;
  placeholder?: string | null;
  title?: string | null;
};

function tidy(s: string | null | undefined): string {
  const t = (s ?? "").replace(/\s+/g, " ").replace(/[.:…]+$/u, "").trim();
  return t.length > MAX_FIELD ? `${t.slice(0, MAX_FIELD - 1).trimEnd()}…` : t;
}

/** The field's name, from the first thing that names it. Empty when nothing does. */
export function fieldName(n: FieldNaming): string {
  for (const s of [n.ariaLabel, n.labelledBy, n.label, n.placeholder, n.title]) {
    const t = tidy(s);
    if (t) return t;
  }
  return "";
}

/** "Message in Chats", "Text field in Settings", or just the field. */
export function placeName(field: string, page: string): string {
  const f = field || "Text field";
  return page ? `${f} in ${page}` : f;
}

let page = "";
const listeners = new Set<() => void>();

export const currentPage = {
  get: () => page,
  set(next: string) {
    if (next === page) return;
    page = next;
    listeners.forEach((fn) => fn());
  },
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
};
