// Is the tab running an older build than the server is serving?
//
// WHY THIS EXISTS. The edge already solves this for a FRESH page load:
// `workshop_public_app` serves index.html with `Cache-Control: no-cache` and an
// ETag, precisely so "you deployed but I still see the old version" cannot
// happen to someone who opens the app. Its own comment says so.
//
// It cannot help a tab that is already open. A single-page app never requests
// index.html again, so a tab left open for an afternoon keeps running the
// bundle it loaded, and every fix shipped since is invisible in it. Measured
// 2026-09-01: a UI fix was committed, built, verified live by content on the
// public origin, and the operator, in a tab that had been open for hours, was
// still looking at the previous bundle and correctly said it was not fixed.
//
// NO BANNER. The operator deleted the last one in these words: "you have to
// stop and remove this shitty ass banner and never show this again fucking
// delete it." A notice that says "a new version is available, click to reload"
// makes the reader do the app's job, and it is chrome on every page forever to
// serve a case that arises once a day at most. So there is no UI here at all.
//
// The rule instead: notice quietly, and act on the next NAVIGATION. Changing
// page is already a discontinuity, so a reload there costs nothing the
// navigation was not already costing, and it can never interrupt typing, a
// running turn, or a form, because none of those survive a route change
// anyway.

/** The entry bundle's filename, which Vite content-hashes, so it changes when
 *  and only when the build does. Null when the html names none, which is what
 *  an error page or a proxy interstitial looks like: not a new build. */
export function entryFromHtml(html: string): string | null {
  const m = html.match(/assets\/index-[A-Za-z0-9_-]+\.js/);
  return m ? m[0] : null;
}

/** The entry this tab is running, read from its own document. */
export function currentEntry(doc: Pick<Document, "querySelectorAll">): string | null {
  const scripts = Array.from(doc.querySelectorAll("script[src]")) as HTMLScriptElement[];
  for (const s of scripts) {
    const hit = entryFromHtml(s.getAttribute("src") || "");
    if (hit) return hit;
  }
  return null;
}

/**
 * Whether a reload is warranted.
 *
 * BOTH must be known and they must differ. An unknown on either side means the
 * question was not answered, and a reload on an unanswered question is a reload
 * loop: if the poll ever fails in a way that yields null, this would reload,
 * come back, fail again, and reload forever.
 */
export function isStale(current: string | null, latest: string | null): boolean {
  return !!current && !!latest && current !== latest;
}
