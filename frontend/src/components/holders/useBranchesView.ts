// The reads behind the Branches page, joined into one row per branch.
//
// FIVE SOURCES, AND EACH ONE ANSWERS SOMETHING THE OTHERS CANNOT:
//
//   GET /branches?live=true      the trees that exist, read from the DISK and
//                                joined with the records that describe them.
//                                Reading it from the record table alone is what
//                                made this page report eleven stranded trees as
//                                none: most trees were never allocated through
//                                the record path, so most trees had no row.
//   GET /leases                  what is CLAIMED, including claims nobody is
//                                behind any more. This is the read that makes
//                                stranded work visible; see the route's own
//                                header for why the directory cannot do it.
//   GET /presence/directory      which of those holders is a live session, and
//                                what it says it is doing
//   GET /git/status              whether the tree is DIRTY, which is the other
//                                half of "unsaved work"
//   GET /git/ahead-behind        how far the branch has moved from its base
//
// ONLY ONE OF THOSE ROUTES IS NEW. `/leases` was added for this view because
// the fact had no endpoint at all; the working-tree reads are the `/git` router
// that already existed, addressed per tree, rather than a new aggregate that
// would have been a second opinion about the same `git status`.
//
// `/drift` WAS THE SIXTH SOURCE AND IS GONE. It backed one always-mounted
// banner, the operator deleted the banner on 2026-08-28, and an endpoint with
// no reader is dead weight. `bash scripts/apply.sh --check` answers the same
// question from a terminal.
//
// THE PER-TREE READS ARE ONE QUERY EACH, NOT TWO. `useQueries` fans out over
// the branches, and each entry does both git calls in its own `queryFn`. A
// caller wants dirty and ahead/behind together or not at all, so splitting them
// would double the requests in flight to answer one question, and would let a
// row render with half its state.
//
// CADENCES ARE DELIBERATELY DIFFERENT, and they follow what can actually
// change. A lease is renewed every 30s and lapses after 90s, so reading it at
// 15s is inside the window where a holder can vanish without this view being
// wrong for long. A branch record changes when a tree is allocated or reaped,
// which is minutes apart. A working tree changes whenever anyone types, but
// reading it costs a subprocess per branch, so it sits in between.
import { useQueries, useQuery } from "@tanstack/react-query";
import { shortHttpError } from "@workshop/ui";

import type {
  BranchRow, LeaseRow, LeaseWindows, TreeState,
} from "../../lib/branchesView";
import { holderFor, type BranchRecord, type DirectoryEntry } from "../../lib/holders";
import { queryKeys } from "../../lib/queryKeys";

const DIRECTORY_MS = 15_000;
const LEASES_MS = 15_000;
const BRANCHES_MS = 60_000;
const TREE_MS = 30_000;

async function readJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as T;
}

/** The windows the SERVER derives its states from. Used until `/leases`
 *  answers, and then replaced by what it said. It is not a copy of the
 *  constants: it is a placeholder so the first paint has numbers, and every
 *  rendered value comes from the response the moment there is one. */
const WINDOWS_UNTIL_LOADED: LeaseWindows = {
  lease_ttl_sec: 90, heartbeat_sec: 30, idle_after_sec: 600, preempt_grace_sec: 30,
};

export type BranchesViewRead = {
  /** Null while cold. An empty array is an answer: no trees are allocated. */
  rows: BranchRow[] | null;
  windows: LeaseWindows;
  /** False when this installation keeps no lease records at all, which is a
   *  different thing from holding none and must not render the same. */
  leasesAvailable: boolean;
  /** False when the relational store could not be read. The trees below are
   *  still real (they came from git); they carry no ticket, no allocator and no
   *  status. Different from "no trees", and rendered as such. */
  recordsAvailable: boolean;
  /** The read that failed, if one did, named with the route that failed it. A
   *  SECONDARY failure leaves the rows on screen, so the caller shows this as a
   *  line beside them and not as a region over them. */
  err: string | null;
  /** The branch list itself could not be read, so there are no rows and there
   *  is nothing for a line to sit beside.
   *
   *  SEPARATE FROM `err` BECAUSE THEY PICK DIFFERENT SHAPES, and conflating
   *  them was a real bug: with the branches loaded and `/leases` failing, the
   *  page showed an error REGION reading "GET /api/branches" over a list that
   *  had loaded perfectly well, hiding the honest "nothing is stuck" answer
   *  behind the name of a route that had not failed. */
  fatal: string | null;
  /** True only before the FIRST answer of the two reads a row cannot exist
   *  without. A refetch is not loading; the rows are still there. */
  loading: boolean;
  reload: () => void;
};

export function useBranchesView({ enabled = true }: { enabled?: boolean } = {})
: BranchesViewRead {
  const branches = useQuery({
    queryKey: queryKeys.presence.branches("all"),
    queryFn: async (): Promise<{ records: BranchRecord[]; recordsAvailable: boolean }> => {
      const d = await readJson<{
        worktrees: BranchRecord[]; records_available: boolean;
      }>("/api/branches?live=true&limit=500");
      return {
        records: d.worktrees ?? [],
        // `undefined` from an older backend reads as available, for the reason
        // `leasesAvailable` gives below: "this installation keeps no records" is
        // a strong claim and must not be made before it has been asserted.
        recordsAvailable: d.records_available ?? true,
      };
    },
    enabled,
    refetchInterval: BRANCHES_MS,
    staleTime: 0,
    retry: false,
  });

  const leases = useQuery({
    queryKey: queryKeys.leases.list("worktree"),
    queryFn: async () => readJson<{
      leases: LeaseRow[]; available: boolean; windows: LeaseWindows;
    }>("/api/leases?subject_kind=worktree&held_only=false&limit=500"),
    enabled,
    refetchInterval: LEASES_MS,
    staleTime: 0,
    retry: false,
  });

  const directory = useQuery({
    queryKey: queryKeys.presence.directory("worktree"),
    queryFn: async (): Promise<DirectoryEntry[]> => {
      const d = await readJson<{ entries: DirectoryEntry[] }>(
        "/api/presence/directory?subject_kind=worktree");
      return d.entries ?? [];
    },
    enabled,
    refetchInterval: DIRECTORY_MS,
    staleTime: 0,
    retry: false,
  });

  const records = branches.data?.records ?? null;

  // One entry per tree. `combine` folds the array into a lookup so the caller
  // re-renders on the whole set rather than on each arrival, which is what
  // useQueries is for.
  const trees = useQueries({
    queries: (records ?? []).map((r) => ({
      queryKey: queryKeys.git.treeState(r.path, r.base_ref),
      queryFn: async (): Promise<{ tree: TreeState; ahead: number | null; behind: number | null }> => {
        const [status, ab] = await Promise.all([
          readJson<{ clean: boolean; entries: unknown[] }>(
            `/api/git/status?repo=${encodeURIComponent(r.path)}`),
          // A base that is not fetched, or a tree cut from a ref that has since
          // gone, answers nothing rather than failing the row: how dirty a tree
          // is matters more here than how far it has drifted.
          readJson<{ ahead: number; behind: number }>(
            `/api/git/ahead-behind?repo=${encodeURIComponent(r.path)}`
            + `&upstream=${encodeURIComponent(r.base_ref)}`).catch(() => null),
        ]);
        return {
          tree: { clean: !!status.clean, entries: (status.entries ?? []).length },
          ahead: ab?.ahead ?? null,
          behind: ab?.behind ?? null,
        };
      },
      // ONLY A TREE GIT KNOWS ABOUT CAN ANSWER `git status`. A `gone` row has no
      // directory and an `orphan` row has no git metadata, so both would 4xx on
      // every poll and put a permanent "one of the reads did not land" line
      // beside a table that is entirely correct.
      enabled: enabled && !!r.path && r.presence === "tree",
      refetchInterval: TREE_MS,
      staleTime: 0,
      retry: false,
    })),
    combine: (results) => results.map((q) => q.data ?? null),
  });

  const leaseFor = new Map<string, LeaseRow>();
  for (const l of leases.data?.leases ?? []) leaseFor.set(l.subject_id, l);

  const entries = directory.data ?? null;

  const rows: BranchRow[] | null = records === null ? null : records.map((record, i) => {
    const t = trees[i] ?? null;
    const lease = leaseFor.get(record.id) ?? null;
    return {
      record,
      lease,
      // The live session behind the lease, when there is one. `holderFor`
      // already knows that more than one session can be present on a subject
      // and that the LEASE names which of them is the holder.
      holder: entries ? holderFor(entries, record.id) : null,
      tree: t?.tree ?? null,
      // Filled by `useYieldFlags` below, which only asks about the sessions
      // that actually hold something.
      yieldRequested: false,
      ahead: t?.ahead ?? null,
      behind: t?.behind ?? null,
    };
  });

  return {
    rows,
    windows: leases.data?.windows ?? WINDOWS_UNTIL_LOADED,
    // `undefined` while cold reads as available, because "this installation
    // keeps no leases" is a strong claim and must not be made before asking.
    leasesAvailable: leases.data?.available ?? true,
    recordsAvailable: branches.data?.recordsAvailable ?? true,
    // Named by route, so a line beside the table says which read went missing
    // rather than blaming whichever one the caller happens to mention.
    err: [
      branches.error ? `GET /api/branches: ${branches.error}` : null,
      leases.error ? `GET /api/leases: ${leases.error}` : null,
      directory.error ? `GET /api/presence/directory: ${directory.error}` : null,
    ].filter(Boolean)[0] ?? null,
    fatal: branches.isError && branches.data === undefined
      ? String(branches.error) : null,
    loading: branches.data === undefined && !branches.isError,
    reload: () => {
      void branches.refetch();
      void leases.refetch();
      void directory.refetch();
    },
  };
}

/**
 * Which holders have been asked to yield, i.e. have a preempt in flight.
 *
 * A SEPARATE HOOK, AND ASKED ONLY ABOUT HOLDERS THAT EXIST. The control channel
 * is addressed per session, so this is one request per live holder and there is
 * no route that answers "every channel with a yield on it" for chats (the
 * `/control` list route says so itself, and explains that a chat's requests live
 * in its own thread's queue). Asking per holder is therefore the honest read,
 * and it is bounded by the number of held trees rather than by the number of
 * branches.
 */
export function useYieldFlags(rows: BranchRow[] | null, enabled = true): Set<string> {
  const holderIds = [...new Set(
    (rows ?? [])
      .filter((r) => r.lease?.state === "held")
      .map((r) => r.lease!.holder_id)
      .filter(Boolean),
  )];

  const flags = useQueries({
    queries: holderIds.map((id) => ({
      queryKey: queryKeys.presence.control(id),
      queryFn: async (): Promise<{ id: string; yielding: boolean }> => {
        const d = await readJson<{ yield_requested_at: string | null }>(
          `/api/control/${encodeURIComponent(id)}`);
        return { id, yielding: !!d.yield_requested_at };
      },
      enabled,
      refetchInterval: DIRECTORY_MS,
      staleTime: 0,
      retry: false,
    })),
    combine: (results) => new Set(
      results.map((q) => q.data).filter((d): d is { id: string; yielding: boolean } =>
        !!d && d.yielding).map((d) => d.id),
    ),
  });

  return flags;
}
