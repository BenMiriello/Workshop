// What a reviewer can work out from a change and its diff, with no help from
// the author.
//
// THIS IS THE POINT OF THE MODULE. `changes-and-review-2026-08-22.md` sec 5:
// the measured top defect in agent-authored changes is a description claiming
// things the diff does not contain (45.4% of inconsistent PRs), and here the
// same worker writes both the code and the claim. So every number the review
// surface puts on screen -- per-file adds and removes, the totals, the size
// band, the risk signals -- is DERIVED FROM THE PATCH TEXT, never from a field
// the author filled in.
//
// Pure on purpose: the frontend suite has no DOM harness, so the target of a
// test is always the rule behind the render (`ticketDecision`, `workRows`,
// `places`). Everything here is executed by `changeReview.test.ts`.

import { shortHttpError, type StatusKind } from "@workshop/ui";
import type { components } from "../api/schema.gen";

export type Change = components["schemas"]["Change"];
export type ChangeDiff = components["schemas"]["ChangeDiff"];
export type ChangeFileDiff = components["schemas"]["ChangeFileDiff"];
export type ChangeGate = components["schemas"]["ChangeGate"];

// ---- status -----------------------------------------------------------------
// The six the record can hold (routes/changes.py, design doc sec 3).

export const CHANGE_STATUSES = [
  "draft",
  "ready",
  "changes_requested",
  "approved",
  "merged",
  "closed",
] as const;
export type ChangeStatus = (typeof CHANGE_STATUSES)[number];

// `ready` and `changes_requested` are both `warn`: one wants the operator's
// attention, the other wants the worker's, and neither is idle. `draft` and
// `closed` are `stopped` because nothing is waiting on anybody.
const STATUS_KIND: Record<ChangeStatus, StatusKind> = {
  draft: "stopped",
  ready: "warn",
  changes_requested: "warn",
  approved: "ok",
  merged: "ok",
  closed: "stopped",
};

const STATUS_LABEL: Record<ChangeStatus, string> = {
  draft: "draft",
  ready: "ready to review",
  changes_requested: "changes requested",
  approved: "approved",
  merged: "merged",
  closed: "closed",
};

export const changeStatusKind = (status?: string): StatusKind =>
  STATUS_KIND[(status ?? "") as ChangeStatus] ?? "stopped";

export const changeStatusLabel = (status?: string): string =>
  STATUS_LABEL[(status ?? "") as ChangeStatus] ?? String(status ?? "");

/** Still in play: not merged, not closed. What "open changes" means. */
export const isOpenChange = (c: Pick<Change, "status">): boolean =>
  c.status !== "merged" && c.status !== "closed";

// A gate's own vocabulary is the checker's, not this system's, so the mapping
// is generous about spelling and falls through to `warn` rather than guessing
// green.
export function gateKind(status?: string): StatusKind {
  const s = (status ?? "").trim().toLowerCase();
  if (s === "pass" || s === "passed" || s === "ok" || s === "green" || s === "success") return "ok";
  if (s === "fail" || s === "failed" || s === "error" || s === "red") return "danger";
  if (s === "running" || s === "pending" || s === "queued") return "running";
  if (s === "skip" || s === "skipped" || s === "n/a") return "stopped";
  return "warn";
}

// ---- per-file status letter -------------------------------------------------
// git's `--name-status` letter, verbatim from `worktrees.diff_branch`. A rename
// or a copy carries a similarity score (`R100`), so match on the first letter.

export function fileStatusLabel(status?: string): string {
  switch ((status ?? "").trim().charAt(0).toUpperCase()) {
    case "A": return "added";
    case "D": return "deleted";
    case "M": return "modified";
    case "R": return "renamed";
    case "C": return "copied";
    case "T": return "type changed";
    case "U": return "unmerged";
    default: return (status ?? "").trim().toLowerCase() || "changed";
  }
}

export const isDeletedFile = (status?: string): boolean =>
  (status ?? "").trim().charAt(0).toUpperCase() === "D";

// ---- reading the patch ------------------------------------------------------

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "context";

/**
 * What one line of a unified diff is.
 *
 * The `+++`/`---` file headers are checked BEFORE the bare `+`/`-` lines: they
 * start with the same characters, and counting them is how a one-line edit
 * reports as two added and two removed.
 */
export function diffLineKind(line: string): DiffLineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (
    line.startsWith("diff --git ") ||
    line.startsWith("index ") ||
    line.startsWith("new file mode ") ||
    line.startsWith("deleted file mode ") ||
    line.startsWith("old mode ") ||
    line.startsWith("new mode ") ||
    line.startsWith("similarity index ") ||
    line.startsWith("rename from ") ||
    line.startsWith("rename to ") ||
    line.startsWith("copy from ") ||
    line.startsWith("copy to ") ||
    line.startsWith("Binary files ") ||
    line.startsWith("\\ No newline")
  ) {
    return "meta";
  }
  return "context";
}

export type FileStat = {
  added: number;
  removed: number;
  /** Lines of patch text, which is what decides truncation and default state. */
  lines: number;
  binary: boolean;
};

/** Adds and removes for one file, counted off its own patch text. */
export function fileStat(diff: string): FileStat {
  const text = diff ?? "";
  const lines = text ? text.split("\n") : [];
  let added = 0;
  let removed = 0;
  let binary = false;
  for (const line of lines) {
    if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) binary = true;
    const kind = diffLineKind(line);
    if (kind === "add") added++;
    else if (kind === "del") removed++;
  }
  return { added, removed, lines: lines.length, binary };
}

export type DiffTotals = { files: number; added: number; removed: number; changed: number };

export function diffTotals(files: ChangeFileDiff[]): DiffTotals {
  let added = 0;
  let removed = 0;
  for (const f of files) {
    const s = fileStat(f.diff);
    added += s.added;
    removed += s.removed;
  }
  return { files: files.length, added, removed, changed: added + removed };
}

// ---- size -------------------------------------------------------------------
// "Reviewer accuracy degrades with diff size" (design doc sec 5.6, 9.3). The
// bands are the ones the review literature keeps landing on: a couple of
// hundred changed lines is about where a careful read stops being possible in
// one sitting.

export type SizeBand = "small" | "medium" | "large" | "huge";

export function sizeBand(changedLines: number): SizeBand {
  if (changedLines <= 50) return "small";
  if (changedLines <= 200) return "medium";
  if (changedLines <= 600) return "large";
  return "huge";
}

export function sizeLabel(band: SizeBand): string {
  switch (band) {
    case "small": return "small";
    case "medium": return "medium";
    case "large": return "large";
    case "huge": return "very large";
  }
}

// ---- risk signals -----------------------------------------------------------

export type RiskSignal = {
  id: "dependencies" | "migration" | "deletions" | "size";
  label: string;
  detail: string;
};

// A manifest or a lockfile: the diff pulls in code nobody in this repo wrote.
const MANIFESTS: RegExp[] = [
  /(^|\/)package\.json$/,
  /(^|\/)package-lock\.json$/,
  /(^|\/)npm-shrinkwrap\.json$/,
  /(^|\/)pnpm-lock\.yaml$/,
  /(^|\/)yarn\.lock$/,
  /(^|\/)requirements[^/]*\.txt$/,
  /(^|\/)pyproject\.toml$/,
  /(^|\/)poetry\.lock$/,
  /(^|\/)Pipfile(\.lock)?$/,
  /(^|\/)Cargo\.(toml|lock)$/,
  /(^|\/)go\.(mod|sum)$/,
  /(^|\/)Gemfile(\.lock)?$/,
];

// A schema move: the one kind of change that is not undone by reverting it.
const MIGRATIONS: RegExp[] = [
  /(^|\/)migrations?\//i,
  /(^|\/)alembic\//i,
  /(^|\/)schema\.sql$/i,
  /(^|\/)\d{3,}[-_][^/]*\.(sql|py|ts|js)$/,
];

const matchesAny = (path: string, patterns: RegExp[]): boolean =>
  patterns.some((re) => re.test(path));

export const touchesManifest = (path: string): boolean => matchesAny(path, MANIFESTS);
export const touchesMigration = (path: string): boolean => matchesAny(path, MIGRATIONS);

/** How many paths to name in a signal's detail before saying "and N more". */
const NAMED_PATHS = 3;

function nameThem(paths: string[]): string {
  if (paths.length <= NAMED_PATHS) return paths.join(", ");
  const rest = paths.length - NAMED_PATHS;
  return `${paths.slice(0, NAMED_PATHS).join(", ")} and ${rest} more`;
}

/**
 * The four things a reviewer should be told before reading a line of the patch,
 * all computed from the patch itself (design doc sec 5.6).
 *
 * Size is in here rather than beside it because it is a risk signal in exactly
 * the same sense, and a caller that had to assemble it separately would be one
 * refactor away from dropping it. Absent signals are absent: this never returns
 * a reassuring "no migrations" row, because a list of non-events is noise.
 */
export function riskSignals(files: ChangeFileDiff[]): RiskSignal[] {
  const out: RiskSignal[] = [];

  const manifests = files.filter((f) => touchesManifest(f.path)).map((f) => f.path);
  if (manifests.length) {
    out.push({
      id: "dependencies",
      label: "Dependencies may have changed",
      detail: `${nameThem(manifests)} touched. Read the added lines before accepting.`,
    });
  }

  const migrations = files.filter((f) => touchesMigration(f.path)).map((f) => f.path);
  if (migrations.length) {
    out.push({
      id: "migration",
      label: "Schema migration",
      detail: `${nameThem(migrations)} touched. Merging this moves data, and reverting the merge does not move it back.`,
    });
  }

  const deleted = files.filter((f) => isDeletedFile(f.status)).map((f) => f.path);
  if (deleted.length) {
    out.push({
      id: "deletions",
      label: `${deleted.length} file${deleted.length === 1 ? "" : "s"} deleted`,
      detail: nameThem(deleted),
    });
  }

  const totals = diffTotals(files);
  const band = sizeBand(totals.changed);
  if (band === "large" || band === "huge") {
    out.push({
      id: "size",
      label: `${sizeLabel(band)} diff`,
      detail: `${totals.changed} changed lines across ${totals.files} file${totals.files === 1 ? "" : "s"}. Review accuracy falls off with size.`,
    });
  }

  return out;
}

// ---- what the page is allowed to draw ---------------------------------------

/** Files listed before the list says how many it is not showing. */
export const FILE_LIST_CAP = 40;

/** Patch lines drawn for one file before the body says how many it clipped. */
export const DIFF_LINE_CAP = 400;

/**
 * Whether a file's patch is open when the page first draws.
 *
 * A one-file change should read as a diff, not as a closed drawer; forty files
 * should not paint forty thousand lines. Open only while the whole change is
 * small enough that expanding it all is what a reader wanted anyway.
 */
export function startsExpanded(fileCount: number, stat: FileStat): boolean {
  if (stat.binary) return false;
  if (fileCount > 6) return false;
  return stat.lines <= DIFF_LINE_CAP;
}

export type ClippedDiff = { text: string; clipped: number };

/** The patch text, cut to `cap` lines, with the count of what was cut. */
export function clipDiff(diff: string, cap: number = DIFF_LINE_CAP): ClippedDiff {
  const lines = (diff ?? "").split("\n");
  if (lines.length <= cap) return { text: diff ?? "", clipped: 0 };
  return { text: lines.slice(0, cap).join("\n"), clipped: lines.length - cap };
}

// ---- refusals ---------------------------------------------------------------

/** The `detail` a FastAPI refusal carries, in either of the two shapes ADR 0023
 *  put on the wire. "" when the body is not one of ours. */
function detailOf(body: string): string {
  const b = (body ?? "").trim();
  if (!b.startsWith("{")) return "";
  try {
    const parsed = JSON.parse(b);
    const d = parsed?.detail;
    if (typeof d === "string") return d.trim();
    if (d && typeof d === "object" && typeof d.message === "string") return d.message.trim();
  } catch {
    return "";
  }
  return "";
}

/** A refused decision, capped. The backend already truncates a conflict reason
 *  to 400 characters; this is the belt on top of that so a runaway body cannot
 *  become the page. */
export const REFUSAL_CAP = 800;

/**
 * What to put on screen when `POST /tasks/{scope}/{id}/review` refuses.
 *
 * A 409 is the MERGE CONFLICT answer and it is the one case where the server's
 * own sentence is the whole value of the response: it names the branch state
 * the operator has to go and fix. `shortHttpError` drops any body over 200
 * characters in favour of the status, which turns that sentence into
 * "409 Conflict" and leaves the operator with nothing. So a 409 shows its
 * detail verbatim, and everything else keeps the standard short line.
 */
export function decisionRefusal(status: number, statusText: string, body: string): string {
  if (status === 409) {
    const detail = detailOf(body);
    if (detail) return detail.length > REFUSAL_CAP ? `${detail.slice(0, REFUSAL_CAP)}...` : detail;
  }
  return shortHttpError(status, statusText, body);
}

/**
 * A 503 from `/changes` is not a failed load.
 *
 * `services/changes.py::ChangesUnavailable` is raised when there is no
 * relational store, and it is deliberately NOT answered as an empty list: an
 * empty `/changes` because the database is absent must not read the same as an
 * empty `/changes` because nothing is open. The UI owes the same distinction --
 * "couldn't load, retry" is the wrong sentence for a store that was never
 * configured, and a Retry button on it is a lie.
 */
export const changesUnavailable = (status: number): boolean => status === 503;

// ---- the tickets a change carries -------------------------------------------

export type TicketTarget = { scope: string; id: string };

/**
 * The scope and id behind a resolved `workshop:ticket/<id>` ref.
 *
 * A change stores ticket IDS and nothing else, while every ticket endpoint is
 * addressed `/{scope}/{id}`. The managed-ref resolver is what knows the scope
 * (`services/refs.py::_resolve_ticket` returns `/tickets/<scope>/<id>`), so the
 * href it hands back is the lookup, and this is the one place that reads it.
 * Null for anything that is not a ticket href, so a caller can never build a
 * request against a guess.
 */
export function ticketTargetFromHref(href?: string | null): TicketTarget | null {
  const parts = (href ?? "").split("?")[0].split("/").filter(Boolean);
  if (parts.length !== 3 || parts[0] !== "tickets") return null;
  const scope = decodeURIComponent(parts[1]);
  const id = decodeURIComponent(parts[2]);
  if (!scope || !id) return null;
  return { scope, id };
}

/** The ref string for a ticket id, in the one managed format. */
export const ticketRef = (id: string): string => `workshop:ticket/${id}`;
