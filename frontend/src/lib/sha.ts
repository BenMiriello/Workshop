// Git object ids, abbreviated. Three deploy surfaces each declared the same
// one-line `short(sha)`, byte for byte, and a fourth screen that wanted one
// would have written a fifth. All three are gone now — the last two with the
// deploy subsystem on 2026-09-04 (ticket `726d75a9`) — which is the argument
// for the module rather than against it: the helper outlived every one of its
// original callers without needing an edit.
//
// It lives in `lib/` rather than beside a feature because a commit id is not a
// deploy concept: runs, projects and the dev-mode bundle probe all carry one,
// and any of them is the next caller. It is PURE (no React, no
// fetch) so it is executable by a test, which is the whole reason a
// one-liner is worth a module.

/** The 7-character abbreviation, which is what `git log --oneline` prints and
 * therefore what the operator recognises when comparing a page against a
 * terminal. "" for a missing sha, never "undefined": these render inline in a
 * meta line, on truthiness, exactly like `lib/time.ts`'s formatters. */
export function shortSha(sha?: string | null): string {
  return (sha || "").slice(0, 7);
}
