// Reading the ownership surfaces: who is here, and what branches exist.
//
// TWO READS, BOTH POLLED, NEITHER WRITTEN TO. `GET /presence/directory` is a
// read-time join that stores nothing (leases design section 6), so there is no
// "invalidate" to do and nothing here can be wrong for longer than one
// interval. `GET /branches` is the worktree RECORD.
//
// Both run on the query layer (`lib/queryClient.ts`) at the intervals below,
// which is where the pausing, the catch-up read on the way back from hidden and
// the shared in-flight request come from.
//
// Both hooks report `entries === null` while cold and a message on `err`, so
// every caller can draw the four list states from one shape. A poll that fails
// keeps the last good payload: the rows are still on screen, so the failure is
// a line beside them and not a region over them (frontend/CLAUDE.md).
//
// Both are now keyed by what they asked for, which the `useState` pair they
// replace was not: changing the subject kind, or the app, used to leave the
// previous answer on screen until the next read landed. That window is the cold
// state now, and coming back to a filter already read is an instant cache hit.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { shortHttpError } from "@workshop/ui";
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
  // The filter is in the key, so two surfaces asking about different subject
  // kinds cannot be served each other's answer.
  const q = useQuery({
    queryKey: ["presence", "directory", subjectKind ?? null],
    queryFn: async (): Promise<DirectoryEntry[]> => {
      const qs = subjectKind ? `?subject_kind=${encodeURIComponent(subjectKind)}` : "";
      const d = await readJson<{ entries: DirectoryEntry[] }>(`/api/presence/directory${qs}`);
      return d.entries ?? [];
    },
    // `enabled: false` is what the closed sheet passed to `usePoll`: no timer,
    // no read, and the hook still reports cold.
    enabled,
    refetchInterval: DIRECTORY_MS,
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  return {
    entries: q.data ?? null,
    // Set on a failed read and cleared by the next SUCCESSFUL one, which is
    // what `isError` already means.
    err: q.isError ? String(q.error) : null,
    reload: () => { void q.refetch(); },
  };
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
  const addr = projectSlug
    ? `project=${encodeURIComponent(projectSlug)}`
    : appId ? `app_id=${encodeURIComponent(appId)}` : "";

  const q = useQuery({
    queryKey: ["branches", addr],
    // AN APP WITH NO TREE ANSWERS `[]` WITHOUT ASKING, which is what the `if
    // (!key)` at the top of the load this replaces did. It is not `enabled:
    // false`: that would report the hook as cold forever, and "this app has no
    // branches" is an answer, not an absence of one.
    queryFn: async (): Promise<BranchRecord[]> => {
      if (!addr) return [];
      const d = await readJson<{ worktrees: BranchRecord[] }>(`/api/branches?${addr}&live=true`);
      return d.worktrees ?? [];
    },
    // `enabled: false` is what a closed surface passed to `usePoll`: no timer,
    // no read, and the hook still reports cold.
    enabled,
    refetchInterval: BRANCHES_MS,
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  return {
    records: q.data ?? null,
    err: q.isError ? String(q.error) : null,
    reload: () => { void q.refetch(); },
  };
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
