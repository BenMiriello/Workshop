// Which MCP servers a chat's turns may reach.
//
// The stored value is `string[] | null`, and NULL IS NOT "NONE" — it is "all
// servers", the state of every chat that has never opened the control. That
// asymmetry is the whole reason this module exists as pure, tested rules rather
// than as `useState` inside a modal: getting it backwards would silently strip
// the tools off every existing conversation, and the symptom (a chat that says
// it cannot do something it could do yesterday) points nowhere near the cause.
//
// The other rule worth stating once: TICKING EVERY BOX MEANS "ALL", not "these
// eight". A frozen list of today's servers would arrive at next month's
// newly-installed server switched off, in a chat whose UI shows everything on.
// `collapse` is where that is decided on this side; chat_store's
// normalize_mcp_selection decides it identically on the other, and the backend
// is the authority — this one exists so the UI does not flicker between saying
// "all" and "8 of 8" while a save is in flight.

/** null = every server. A list = exactly those. [] = none, which is a real and
 *  deliberately reachable choice. */
export type McpSelection = string[] | null;

/** Is this server enabled for the chat? Unknown to the selection means off,
 *  but an ABSENT selection means everything is on. */
export function isEnabled(sel: McpSelection, name: string): boolean {
  return sel === null || sel.includes(name);
}

/** The selection with `name` flipped, collapsed back to null when the result
 *  covers everything the machine currently has. */
export function toggle(sel: McpSelection, name: string, all: string[]): McpSelection {
  const on = sel === null ? all.slice() : sel.slice();
  const at = on.indexOf(name);
  if (at >= 0) on.splice(at, 1);
  else on.push(name);
  return collapse(on, all);
}

/** A list naming every known server IS "all", and is stored as null. */
export function collapse(sel: string[], all: string[]): McpSelection {
  const on = new Set(sel);
  return all.every((n) => on.has(n)) ? null : Array.from(on);
}

/** How many of `all` are on. Reads the same whether or not a choice was made. */
export function enabledCount(sel: McpSelection, all: string[]): number {
  return sel === null ? all.length : all.filter((n) => sel.includes(n)).length;
}

/** "all 8 enabled" / "3 of 8 enabled". Always says the total: "3 enabled" alone
 *  does not tell the operator whether anything is switched off. */
export function selectionSummary(sel: McpSelection, all: string[]): string {
  const total = all.length;
  if (!total) return "";
  const n = enabledCount(sel, all);
  return n === total ? `all ${total} enabled` : `${n} of ${total} enabled`;
}

// A selection has to survive `useEditMode`, which compares drafts with a
// SHALLOW equality — two arrays with the same contents are two different
// objects, so a draft holding an array is dirty from the moment it is created
// and Save never settles. Encoding it as one string makes the draft comparable
// by value, which is what that hook's contract actually requires.

/** The sentinel for "all servers". `*` cannot collide with a server name: a
 *  name is a key in the CLI's own config, and the empty-string case is handled
 *  separately below. */
const ALL = "*";
const SEP = "\n";

export function encode(sel: McpSelection): string {
  if (sel === null) return ALL;
  // Sorted, so two selections with the same members always encode identically
  // and a pure reordering never reads as an edit.
  return sel.slice().sort().join(SEP);
}

export function decode(s: string): McpSelection {
  if (s === ALL) return null;
  if (s === "") return [];
  return s.split(SEP);
}
