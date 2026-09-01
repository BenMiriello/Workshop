// A `Page` envelope's `total` is allowed to be NULL, and null does not mean zero.
//
// ADR 0023 clause 1 says so in words and `GET /api/jobs` says so in practice:
// `jobs.read` collapses an append-only log by id and stops at `limit`, so
// counting the filtered set would mean a second full pass over every line ever
// written. The route therefore answers `page: { limit, total: null }` at every
// limit, and its own docstring closes with "a null total must not be read as
// zero".
//
// The idiom that reads it was `data?.page?.total ?? 0`, which does exactly that:
// it renders 0 over a list that has rows in it. That is the same shape as the
// failure this whole area exists to prevent - a number the reader cannot tell
// apart from "there are none" - just with a count instead of rows. Measured
// 2026-09-01 against the live backend: `/api/jobs?limit=200` returns 200 items
// and `page.total: null`.
//
// So the fallback is the rows in hand, never 0. It understates when the server
// declined to count, which is honest, and it can never claim a full list is
// empty.

/** The row count a list may claim: the server's own total when it counted, and
 *  the rows actually in hand when it did not. Never 0 over a non-empty list. */
export const rowTotal = (total: number | null | undefined, shown: number): number =>
  typeof total === "number" ? total : shown;

/** Whether the server actually counted. A list that only has `shown` cannot
 *  offer a page count, so a pager asks this before drawing one. */
export const isCounted = (total: number | null | undefined): boolean =>
  typeof total === "number";
