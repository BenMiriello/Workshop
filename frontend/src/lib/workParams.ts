// The /work URL, parsed and written in one place.
//
// `?scope=` was read once as initial state, so navigating to `/work?scope=X`
// from a page that was already /work changed the URL and nothing else. It DRIVES
// the filter now, which is why the parsing lives here rather than inline.
//
// `?tab=` is gone (2026-08-12). The page had four tabs (Open / Runs / Review /
// Inbox) that were not four kinds of thing, and it is one list now. The Runs
// feed moved to /runs; Review read a field that is empty on every ticket in the
// store; the Inbox tab was the same set for a single operator. Old `?tab=` links
// still resolve: an unknown param is ignored and lands on the list.
//
// `?status=` replaces it, and is a filter rather than a mode. It is what makes
// "3 in other scopes" in the chat drawer able to point somewhere true.

// "all" is the absence of a scope filter, and absence is spelled by leaving the
// param off, so the URL of an unfiltered list is plain `/work`.
export const ALL_SCOPES = "all";

export function parseWorkScope(raw: string | null | undefined): string {
  const v = (raw ?? "").trim();
  return v ? v : ALL_SCOPES;
}

// The status filter, as a URL param. Validation belongs to lib/ticketGroups
// (which owns the vocabulary); this only says present-or-absent, so an
// unrecognised value falls back to the page default rather than showing nothing.
export function parseWorkStatus(raw: string | null | undefined): string | null {
  const v = (raw ?? "").trim();
  return v ? v : null;
}

// Build the next query string for /work, preserving everything not named here.
// `null` deletes a param; `undefined` leaves it untouched.
export function nextWorkParams(
  current: URLSearchParams,
  patch: { scope?: string | null; status?: string | null },
): URLSearchParams {
  const next = new URLSearchParams(current);
  if (patch.scope !== undefined) {
    if (patch.scope === null || patch.scope === ALL_SCOPES) next.delete("scope");
    else next.set("scope", patch.scope);
  }
  if (patch.status !== undefined) {
    if (patch.status === null) next.delete("status");
    else next.set("status", patch.status);
  }
  return next;
}
