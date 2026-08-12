// The /work URL, parsed and written in one place.
//
// `?tab=` was written by five navigations and read by nobody: Work held its tab
// in `useState("tickets")`, so a button labelled "Back to runs" landed on the
// ticket list. `?scope=` was read once, as initial state, so navigating to
// `/work?scope=X` from a page that was already /work changed the URL and
// nothing else.
//
// Both are now the single source of truth for the view, which is why the
// parsing lives here rather than inline: a param nothing validates is a param
// that accepts an unknown tab and silently shows a different one.
//
// `inbox` was exactly that case for months. The chat's inbox footer link has
// always navigated to `/work?tab=inbox`, this list held three tabs, and
// `parseWorkTab` fell back to `tickets` — so the link "worked" and landed
// somewhere else. The global inbox exists on the backend
// (`inbox.list_items(chat_scope=None)`), so the fix was the fourth tab, not a
// fifth guard. Adding a value here is half of adding a tab: Work.tsx must
// render it, and the exhaustive check there is what makes the compiler say so.

export const WORK_TABS = ["tickets", "runs", "review", "inbox"] as const;

export type WorkTab = (typeof WORK_TABS)[number];

export const DEFAULT_WORK_TAB: WorkTab = "tickets";

export function isWorkTab(v: string | null | undefined): v is WorkTab {
  return !!v && (WORK_TABS as readonly string[]).includes(v);
}

// An absent or unrecognised tab falls back rather than rendering nothing. An
// unknown value is a caller bug, not an operator-visible error state.
export function parseWorkTab(raw: string | null | undefined): WorkTab {
  return isWorkTab(raw) ? raw : DEFAULT_WORK_TAB;
}

// "all" is the absence of a scope filter, and absence is spelled by leaving the
// param off — so the URL of an unfiltered list is plain `/work`.
export const ALL_SCOPES = "all";

export function parseWorkScope(raw: string | null | undefined): string {
  const v = (raw ?? "").trim();
  return v ? v : ALL_SCOPES;
}

// Build the next query string for /work, preserving everything not named here.
// `null` deletes a param; `undefined` leaves it untouched.
export function nextWorkParams(
  current: URLSearchParams,
  patch: { tab?: WorkTab | null; scope?: string | null },
): URLSearchParams {
  const next = new URLSearchParams(current);
  if (patch.tab !== undefined) {
    if (patch.tab === null || patch.tab === DEFAULT_WORK_TAB) next.delete("tab");
    else next.set("tab", patch.tab);
  }
  if (patch.scope !== undefined) {
    if (patch.scope === null || patch.scope === ALL_SCOPES) next.delete("scope");
    else next.set("scope", patch.scope);
  }
  return next;
}
