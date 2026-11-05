// Which field a dictation is going into, said in a way that survives the field
// leaving the page and coming back as a new element: the page it was on, the
// first stable thing that names it, and which of the fields named that way it
// was. A field nothing names stably has no identity and cannot be waited for.

export type FieldIdentity = {
  /** The path of the document the field is in (an app frame has its own). */
  path: string;
  /** "id:…", "name:…", "label:…" or "hint:…". */
  key: string;
  /** Its place among the document's fields with the same key. */
  index: number;
};

export type FieldKeys = {
  id?: string | null;
  name?: string | null;
  ariaLabel?: string | null;
  placeholder?: string | null;
};

// Ids a framework mints per render (React's useId, ":r3:") name nothing.
const MINTED = /^(:r[0-9a-z]*:|«r[0-9a-z]*»|radix-|headlessui-)/i;

export function stableKey(k: FieldKeys): string {
  const id = k.id?.trim();
  if (id && !MINTED.test(id)) return `id:${id}`;
  const pairs: [string, string | null | undefined][] = [["name", k.name], ["label", k.ariaLabel], ["hint", k.placeholder]];
  for (const [kind, v] of pairs) {
    const t = v?.replace(/\s+/g, " ").trim();
    if (t) return `${kind}:${t}`;
  }
  return "";
}

export function sameIdentity(a: FieldIdentity | null, b: FieldIdentity | null): boolean {
  return !!a && !!b && a.path === b.path && a.key === b.key && a.index === b.index;
}
