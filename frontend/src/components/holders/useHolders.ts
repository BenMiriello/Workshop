// Reading the ownership surfaces: who is here, and what branches exist.
//
// TWO READS, BOTH POLLED, NEITHER CACHED. `GET /presence/directory` is a
// read-time join that stores nothing (leases design section 6), so there is no
// "invalidate" to do and no local copy that can be wrong for longer than one
// interval. `GET /branches` is the worktree RECORD.
//
// Both hooks report `entries === null` while cold and a message on `err`, so
// every caller can draw the four list states from one shape. A poll that fails
// keeps the last good payload: the rows are still on screen, so the failure is
// a line beside them and not a region over them (frontend/CLAUDE.md).
import { useCallback, useEffect, useState } from "react";
import { shortHttpError, usePoll } from "@workshop/ui";
import { treeWriters, trunkLabel, type BranchRecord, type DirectoryEntry } from "../../lib/holders";

// Presence beats every 30s and a lease's TTL is 90s (design section 5), so a
// 15s read is inside the window in which a holder can change without this view
// being wrong for long. It is the same interval the maker panel polls at.
const DIRECTORY_MS = 15_000;
// Branch records change when a tree is allocated or reaped, which is minutes
// apart at best. Reading them as often as presence would be spending a request
// to learn nothing.
const BRANCHES_MS = 60_000;

async function readJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as T;
}

export type DirectoryRead = {
  /** Null while cold. An empty array is an answer: nobody is here. */
  entries: DirectoryEntry[] | null;
  err: string | null;
  reload: () => void;
};

/** Who is here. `enabled` is false while the surface holding this is closed,
 *  so a sheet nobody has opened costs nothing. */
export function useDirectory(
  { enabled = true, subjectKind }: { enabled?: boolean; subjectKind?: string } = {},
): DirectoryRead {
  const [entries, setEntries] = useState<DirectoryEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const q = subjectKind ? `?subject_kind=${encodeURIComponent(subjectKind)}` : "";
  const load = useCallback(async () => {
    try {
      const d = await readJson<{ entries: DirectoryEntry[] }>(`/api/presence/directory${q}`);
      setEntries(d.entries ?? []);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [q]);

  usePoll(load, DIRECTORY_MS, { enabled });
  return { entries, err, reload: () => { void load(); } };
}

export type BranchesRead = {
  records: BranchRecord[] | null;
  err: string | null;
  reload: () => void;
};

/** The live branch records for one app.
 *
 *  Addressed by `app_id` when the app has one and by `project` otherwise: the
 *  record carries both, and an app with no linked project tree has no branches
 *  to read at all, which is `records: []` rather than an error. */
export function useBranches(
  { appId, projectSlug, enabled = true }:
  { appId?: string | null; projectSlug?: string | null; enabled?: boolean },
): BranchesRead {
  const [records, setRecords] = useState<BranchRecord[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const key = projectSlug
    ? `project=${encodeURIComponent(projectSlug)}`
    : appId ? `app_id=${encodeURIComponent(appId)}` : "";

  const load = useCallback(async () => {
    if (!key) { setRecords([]); setErr(null); return; }
    try {
      const d = await readJson<{ worktrees: BranchRecord[] }>(`/api/branches?${key}&live=true`);
      setRecords(d.worktrees ?? []);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [key]);

  usePoll(load, BRANCHES_MS, { enabled });
  return { records, err, reload: () => { void load(); } };
}

// --- the app's ref -----------------------------------------------------------

export type AppRefRead = {
  /** The ref the app's tree actually has checked out, read from git. Null when
   *  the app has no project tree, or the tree could not be read. */
  ref: string | null;
  records: BranchRecord[] | null;
  entries: DirectoryEntry[] | null;
  /** Live sessions holding a lease on one of this app's trees. */
  writers: DirectoryEntry[];
  err: string | null;
  reload: () => void;
};

/** Everything the ref pill and the staleness line on an app page need.
 *
 *  THE REF IS OBSERVED, NOT DERIVED. `GET /projects/{slug}` gives the tree's
 *  directory and `GET /git/rev-parse?abbrev_ref=true` gives the name HEAD is
 *  on, which is the literal answer to "which version am I looking at". The
 *  branch RECORDS (`/branches`) cannot answer it: they describe the trees cut
 *  FROM the trunk, not the trunk itself, so they are the fallback and not the
 *  source. An app with no project tree has no ref, and the pill says so rather
 *  than printing "main" at a repo nobody read.
 */
export function useAppRef(
  { projectSlug, appId, enabled = true }:
  { projectSlug?: string | null; appId?: string | null; enabled?: boolean },
): AppRefRead {
  const [ref, setRef] = useState<string | null>(null);
  const branches = useBranches({ projectSlug, appId, enabled });
  const directory = useDirectory({ enabled });

  useEffect(() => {
    if (!enabled || !projectSlug) { setRef(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const p = await readJson<{ dir?: string; repo?: { default_branch?: string | null } | null }>(
          `/api/projects/${encodeURIComponent(projectSlug)}`);
        if (cancelled) return;
        if (!p.dir) { setRef(p.repo?.default_branch ?? null); return; }
        const rp = await readJson<{ sha?: string | null }>(
          `/api/git/rev-parse?repo=${encodeURIComponent(p.dir)}&rev=HEAD&abbrev_ref=true`);
        if (!cancelled) setRef(rp.sha ?? p.repo?.default_branch ?? null);
      } catch {
        // Not an error state of its own: the pill falls back to what the branch
        // records say they were cut from, and says "no ref recorded" if even
        // that is unavailable. A header control does not get an error region.
        if (!cancelled) setRef(null);
      }
    })();
    return () => { cancelled = true; };
  }, [enabled, projectSlug]);

  const records = branches.records;
  const entries = directory.entries;
  // Both ways of writing this app's tree: a lease on one of its branch trees,
  // and a lease taken while working its scope, which is the trunk.
  const writers = entries
    ? treeWriters(entries, {
      scope: projectSlug ? `project:${projectSlug}` : null,
      subjectIds: (records ?? []).map((r) => r.id),
    })
    : [];

  return {
    ref: trunkLabel(records ?? [], ref),
    records,
    entries,
    writers,
    err: branches.err ?? directory.err,
    reload: () => { branches.reload(); directory.reload(); },
  };
}
