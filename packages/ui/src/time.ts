// The date/time formatting, in ONE place. There were sixteen independent
// implementations across the frontend before this file existed, and they had
// already drifted in ways an operator could see: `CommutePages.tsx` had no day
// tier, so 48 hours read "48h ago" there and "2d ago" everywhere else; some
// callers clamped a negative delta (a clock skew, a server timestamp a beat
// ahead of the browser) and some did not, so a fresh record could read "-3s
// ago"; and the six `fmtTs` copies split into five that agreed and one
// (ProjectDetail's) that used a different format AND a different failure mode:
// it returned the raw, unparsed string instead of "".
//
// Three genuinely distinct jobs, not sixteen:
//   timeAgo            relative, for a list row or a meta line
//   timeAbsolute        full locale stamp, for a tooltip or an "Updated" line
//   timeAbsoluteShort   compact locale stamp, for inline display
// plus one unrelated thing that kept getting redeclared beside them:
//   formatDuration      elapsed wall time between a start and an end
//
// Every one of these fails the same way: "" on a missing or unparseable
// timestamp, never the raw string and never a thrown error. A caller renders
// on truthiness and never has to guess whether what is on screen is a
// formatted stamp or an ISO string that failed to parse.

/** Relative "time ago", the unit every list row and meta line uses.
 *
 * Seconds through years, with a day tier (the bug CommutePages had) and every
 * tier clamped to zero, so a timestamp a beat ahead of the browser's clock
 * reads "0s" rather than a negative number.
 *
 * `suffix: false` drops the trailing " ago" for a caller that already reads
 * as relative from its position (a meta line's own "· 3h", the way
 * `bits.tsx` and the since-deleted `JobsFeed.tsx` used it). Suffixed is the
 * default because most call sites render the string standalone. */
export function timeAgo(iso?: string | null, opts?: { suffix?: boolean }): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const suffix = opts?.suffix ?? true;
  const word = (n: number, unit: string) => (suffix ? `${n}${unit} ago` : `${n}${unit}`);

  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 60) return word(secs, "s");
  const mins = Math.floor(secs / 60);
  if (mins < 60) return word(mins, "m");
  const hours = Math.floor(mins / 60);
  if (hours < 24) return word(hours, "h");
  const days = Math.floor(hours / 24);
  if (days < 30) return word(days, "d");
  const months = Math.floor(days / 30);
  if (months < 12) return word(months, "mo");
  return word(Math.floor(months / 12), "y");
}

/** Full absolute timestamp: the browser's own locale format, date and time
 * together. For a tooltip or an explicit "Updated ..." line, where the extra
 * precision (year, seconds) is worth the width. */
export function timeAbsolute(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

/** Compact absolute timestamp: month, day, hour, minute. No year, because
 * every call site shows something recent enough that the year is implied.
 * For inline display next to content, where a full locale stamp is too wide. */
export function timeAbsoluteShort(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** Elapsed wall time between a start and an end, for a run/job row:
 * "42s", "3m", "3m 12s". "" while the run hasn't started, hasn't ended, or
 * either timestamp fails to parse. */
export function formatDuration(startIso?: string | null, endIso?: string | null): string {
  if (!startIso || !endIso) return "";
  const start = new Date(startIso).getTime();
  const end = new Date(endIso).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** Relative age from an already-measured ELAPSED count, in seconds.
 *
 * This is not `timeAgo` and must not be folded into it. `timeAgo` starts from
 * a timestamp; this starts from an age a caller was handed (`cache.age_s`
 * from the finance API, a launcher probe's own stopwatch), where there is no
 * timestamp to parse and subtracting one from `Date.now()` would only
 * reintroduce the clock skew the API already resolved.
 *
 * It also rounds where `timeAgo` floors, and stops at days where `timeAgo`
 * carries on into months and years. Both were true of the two copies this
 * replaces (`apps/registry.tsx`, `pages/apps/Finance.tsx`), so unifying the
 * ladders would have changed what is on screen: 90 seconds reads "2m ago"
 * here and would read "1m ago" through `timeAgo`, and a 45-day-old cache
 * reads "45d ago" here and "1mo ago" there. A freshness number is read for
 * "how stale is this", where a day count stays legible past a month; a list
 * row is read for "when", where "1mo" is what fits.
 *
 * Callers holding milliseconds divide at the call site, so the unit is
 * visible where it is chosen: the two copies took DIFFERENT units under the
 * same name (`fmtAge(seconds)` and `fmtAge(ms)`), which is a 1000x bug one
 * careless import away. */
export function timeAgoSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "";
  const s = Math.max(0, seconds);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
