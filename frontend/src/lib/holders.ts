// Who is working here, as the ref pill and the directory sheet read it.
//
// THE ONE THING THIS FILE IS FOR: saying who holds a subject and what may be
// done about it, without inventing a state the backend cannot observe.
//
// TWO LEASE STATES, NOT THREE (workspace-ownership-and-leases-2026-08-22.md
// section 6). `LeaseRef.state` is `held` or `expired`. `idle_since` is a
// TIMESTAMP on a held lease: it moves the status KIND to `warn` and it is said
// in the line, and it never becomes a third pill word. A word a reader can key
// on is a state the rest of the system then has to handle.
//
// WHY THERE IS NO `danger` HERE, and this is deliberate rather than an
// omission. Section 14 maps `danger` to "preempted or conflicted", and neither
// is observable from a `LeaseRef`: a preempted lease is simply the next
// generation held by somebody else, and a session present on a subject another
// session holds is the ORDINARY sweep shape (the sweep beats on the ticket its
// own job took the lease on). Painting that pair red would fire on the healthy
// case every time. `danger` belongs to the preempt control, which is where an
// actual destruction is being proposed.
//
// Pure on purpose (frontend/CLAUDE.md: the target of a test is the pure rule
// behind a behaviour). This repo has no DOM harness, so the rules that decide a
// pill's colour, its sentence and which verbs are offered live here and are
// executed by holders.test.ts.
import type { StatusKind } from "@workshop/ui";
import type { components } from "../api/schema.gen";

// Generated from the backend's own OpenAPI schema, never hand-written
// (frontend/CLAUDE.md: "API response types are GENERATED").
export type DirectoryEntry = components["schemas"]["DirectoryEntry"];
export type LeaseRef = components["schemas"]["LeaseRef"];
export type BranchRecord = components["schemas"]["Worktree"];
export type ControlRequest = components["schemas"]["ControlRequest"];
export type ControlAccepted = components["schemas"]["ControlAccepted"];

export type HolderStatus = { kind: StatusKind; label: string };

/** A session id, shortened for a row that has no room for a uuid. Kept as a
 *  whole word where it already is one: `sweep-job_17874…` truncated in the
 *  middle is less legible than the head of it, and run ids are prefixed by
 *  their kind, which is the part worth keeping. */
export function shortId(id: string | null | undefined, keep = 12): string {
  if (!id) return "";
  return id.length <= keep ? id : `${id.slice(0, keep)}…`;
}

/** The four status kinds this view can honestly reach, applied to one live
 *  session and the lease on what it is working.
 *
 *  The case this exists for: A HELD LEASE THAT HAS GONE QUIET IS `warn`, and it
 *  still says "Held". The state did not change, so the word does not; what
 *  changed is that nothing has been written for ten minutes, which is a reason
 *  to look rather than a reason to rename. */
export function holderStatus(
  entry: DirectoryEntry, selfSessionId?: string | null,
): HolderStatus {
  const lease = entry.lease ?? null;
  // Presence with no lease row. The session is live (the directory lists only
  // live presence), it simply holds nothing the lease table knows about, which
  // is the common shape for a chat that is only reading.
  if (!lease) return { kind: "running", label: "Here" };
  if (lease.state === "expired") return { kind: "stopped", label: "Expired" };
  if (selfSessionId && lease.holder_id === selfSessionId) {
    return { kind: "ok", label: "Yours" };
  }
  if (lease.idle_since) return { kind: "warn", label: "Held" };
  return { kind: "running", label: "Held" };
}

/** Who this is, in words: the principal when the beat carried one, else the
 *  kind of session it is, and the id either way because two sweeps in one scope
 *  are told apart by nothing else. */
export function holderName(entry: DirectoryEntry): string {
  const who = (entry.principal || "").trim();
  const id = shortId(entry.session_id);
  return who ? `${who} (${entry.session_kind} ${id})` : `${entry.session_kind} ${id}`;
}

/** What it is doing. The presence channel's own `activity` string, verbatim,
 *  because the writer of the beat knows and this module does not. */
export function activityLine(entry: DirectoryEntry): string {
  return (entry.activity || "").trim() || "No activity reported.";
}

/** The compact form: who, then what it is doing, on one line. This is what a
 *  header control has room for; the sheet has room for the three lines above. */
export function holderLine(entry: DirectoryEntry): string {
  const doing = (entry.activity || "").trim();
  return doing ? `${holderName(entry)}, ${doing}` : holderName(entry);
}

/** What it is working: the subject, named the way the read-time join resolved
 *  it. Null when the session named no subject, which is not an error. */
export function subjectLine(entry: DirectoryEntry): string | null {
  if (!entry.subject_id || entry.subject_kind === "none") return null;
  const label = entry.subject_label || shortId(entry.subject_id, 8);
  const status = entry.subject_status ? ` (${entry.subject_status})` : "";
  return `${entry.subject_kind}: ${label}${status}`;
}

/** How long a held lease has been quiet, in words. Null when it is not idle,
 *  which is every lease whose work moved inside the last ten minutes. */
export function idleFor(entry: DirectoryEntry, nowMs: number): string | null {
  const since = entry.lease?.idle_since;
  if (!since) return null;
  const t = Date.parse(since);
  if (Number.isNaN(t)) return null;
  const mins = Math.max(0, Math.floor((nowMs - t) / 60000));
  if (mins < 60) return `${mins} minutes`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hours`;
  return `${Math.floor(hours / 24)} days`;
}

/** The lease, in one sentence. Two states, and the idle timestamp said as the
 *  fact it is rather than promoted to a state of its own. */
export function leaseLine(entry: DirectoryEntry, nowMs: number): string {
  const lease = entry.lease ?? null;
  if (!lease) {
    return entry.subject_id
      ? "No lease is recorded on what it is working."
      : "It holds nothing.";
  }
  if (lease.state === "expired") {
    return "Its lease expired. Nobody holds this now.";
  }
  if (lease.holder_id !== entry.session_id) {
    // Ordinary, not a conflict: a sweep beats on the ticket its own job took.
    return `Held by ${shortId(lease.holder_id)}, not by this session.`;
  }
  const idle = idleFor(entry, nowMs);
  if (idle) return `Held, but nothing has been written for ${idle}.`;
  return "Held, and still being renewed.";
}

// --- the verbs ---------------------------------------------------------------

export type VerbId = "queue" | "steer" | "preempt";

export type VerbOffer = {
  id: VerbId;
  label: string;
  /** What it actually does. No "now", no "immediately", no "at the next step":
   *  section 8 forbids the UI promising what this system cannot deliver, and
   *  the outcome copy comes back from the route as `detail`. */
  help: string;
  offered: boolean;
  /** Why not, when it is not. Rendered instead of a disabled control. */
  reason?: string;
};

/** Who is asking. Section 8: authority for preempt is bound to a LIVE HUMAN
 *  ACTION, not to a session kind, because every chat acts on the operator's
 *  behalf. An agent queues or asks. */
export type Caller = "operator" | "agent";

/** Which verbs may be offered against this session, in the order they should
 *  be shown: queue first, because queue is the default. */
export function offerableVerbs(
  entry: DirectoryEntry,
  { selfSessionId, caller }: { selfSessionId?: string | null; caller: Caller },
): VerbOffer[] {
  const mine = !!selfSessionId && entry.session_id === selfSessionId;
  const lease = entry.lease ?? null;
  const held = lease?.state === "held" && lease.holder_id === entry.session_id;

  const queue: VerbOffer = {
    id: "queue",
    label: "Add to the queue",
    help: "It takes this when it finishes what it is on. Nothing is interrupted.",
    offered: !mine,
    reason: mine ? "This is your own session." : undefined,
  };
  const steer: VerbOffer = {
    id: "steer",
    label: "Send to the agent",
    // NOT "now" and NOT "immediately". Measured behaviour is that the model
    // addresses a steered message at the end of the plan it is on.
    help: "Delivered into the work in flight. When it acts on it is not guaranteed.",
    offered: !mine,
    reason: mine ? "This is your own session." : undefined,
  };
  // Somebody has to receive the lease, and the route requires that id. A page
  // is not a session, so a surface with no session of its own cannot take a
  // tree over: it can still queue and steer, which are the answers to urgency.
  const canTake = !!selfSessionId;
  const preempt: VerbOffer = {
    id: "preempt",
    label: "Take over",
    help: "Stops it, snapshots its work to a ref you can go back to, and takes the lease.",
    offered: !mine && held && caller === "operator" && canTake,
    reason: mine
      ? "This is your own session."
      : caller !== "operator"
        ? "Only a person acting live can take work away from a session. Queue it or ask."
        : !held
          ? "Nothing is held here, so there is nothing to take."
          : !canTake
            ? "This surface is not a session, so there is nobody to hand the lease to. Take it over from the chat that will do the work."
            : undefined,
  };
  return [queue, steer, preempt];
}

/** The `human_action` a preempt carries. The route requires it and has no
 *  default: it is section 8's authority rule expressed on the wire, so this
 *  string must name the interaction that actually happened. */
export const PREEMPT_HUMAN_ACTION = "take-over confirmed by the operator in the take-over dialog";

/** The ConfirmDialog body. It names what happens to the HOLDER's work, which is
 *  the consequence a person taking a tree away needs to have read. */
export function preemptWarning(entry: DirectoryEntry): string {
  const subject = subjectLine(entry) ?? "what it holds";
  return `${holderName(entry)} is asked to stop, and is stopped if it does not let go within `
    + `the grace window. Its uncommitted work is snapshotted to a dangling ref you can go `
    + `back to. It is not committed, the branch is not moved, and nothing is lost. `
    + `The lease on ${subject} then passes to you.`;
}

// --- the ref -----------------------------------------------------------------

/** What to call the trunk.
 *
 *  `defaultBranch` is the project's own record of it and is used whenever it is
 *  there. Otherwise the branch records say what they were cut FROM, which is
 *  the same name wearing a remote prefix, so the most common `base_ref` with
 *  `origin/` stripped is a real observation rather than a guess. With neither,
 *  this returns null and the caller says it does not know rather than printing
 *  "main" at a repo whose trunk is called something else. */
export function trunkLabel(
  records: BranchRecord[], defaultBranch?: string | null,
): string | null {
  const named = (defaultBranch || "").trim();
  if (named) return named;
  const tally = new Map<string, number>();
  for (const r of records) {
    const ref = (r.base_ref || "").replace(/^origin\//, "").trim();
    if (ref) tally.set(ref, (tally.get(ref) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestN = 0;
  for (const [ref, n] of tally) {
    if (n > bestN) { best = ref; bestN = n; }
  }
  return best;
}

/** The pill's own text. Short, because it sits in a header row beside a page
 *  title, and honest when the trunk has no recorded name. */
export function refPillLabel(trunk: string | null): string {
  return trunk ?? "no ref recorded";
}

/** The status of one branch record, joined to whoever is present on it. A
 *  branch nobody is on is not a problem: it is `stopped`, the neutral outcome
 *  word, because no work is happening there. */
export function branchStatus(
  record: BranchRecord, holder: DirectoryEntry | null, selfSessionId?: string | null,
): HolderStatus {
  if (record.status === "merged") return { kind: "ok", label: "Merged" };
  if (record.status === "discarded") return { kind: "stopped", label: "Discarded" };
  if (!holder) return { kind: "stopped", label: "Nobody here" };
  return holderStatus(holder, selfSessionId);
}

/** Who to name as the holder of one subject.
 *
 *  More than one live session can be present on the same subject (the sweep
 *  beats on the ticket its own job took the lease on), so "the holder" is the
 *  session the LEASE names, and any other present session is a bystander. Falls
 *  back to whoever is there when no lease row matches, because a present
 *  session is still a truthful answer to "who is here". */
export function holderFor(
  entries: DirectoryEntry[], subjectId: string | null | undefined,
): DirectoryEntry | null {
  if (!subjectId) return null;
  const rows = entries.filter((e) => e.subject_id === subjectId);
  return rows.find((e) => e.lease?.holder_id === e.session_id) ?? rows[0] ?? null;
}

/** The live sessions holding a lease on any of these subjects, excluding your
 *  own. This is what "somebody else is writing what you are reading" means in
 *  terms the presence channel can actually answer. */
export function writersOf(
  entries: DirectoryEntry[], subjectIds: string[], selfSessionId?: string | null,
): DirectoryEntry[] {
  const wanted = new Set(subjectIds.filter(Boolean));
  return entries.filter((e) => {
    if (!e.subject_id || !wanted.has(e.subject_id)) return false;
    if (selfSessionId && e.session_id === selfSessionId) return false;
    return e.lease?.state === "held";
  });
}

/** Everyone writing the tree this surface is reading.
 *
 *  Two ways to be that, and both are needed. A session can hold a lease on one
 *  of this app's BRANCH trees, which the records name. Or it can be working the
 *  app's own scope, holding a ticket lease, in which case the tree it is writing
 *  is the app's trunk, and no worktree record exists for the trunk to match on.
 *  Leaving the second out would mean the common case (a sweep working this
 *  project) never raised the staleness line at all. */
export function treeWriters(
  entries: DirectoryEntry[],
  { scope, subjectIds, selfSessionId }:
  { scope?: string | null; subjectIds?: string[]; selfSessionId?: string | null },
): DirectoryEntry[] {
  const wanted = new Set((subjectIds ?? []).filter(Boolean));
  return entries.filter((e) => {
    if (selfSessionId && e.session_id === selfSessionId) return false;
    if (e.lease?.state !== "held") return false;
    if (e.subject_id && wanted.has(e.subject_id)) return true;
    return !!scope && e.scope === scope;
  });
}

/** Reader staleness (section 11), said and not solved.
 *
 *  Contention is loud and staleness is silent, so this exists to make it
 *  audible. It is deliberately ONLY an indication: section 11 is explicit that
 *  no invalidation protocol fixes a read that is already in a session's context
 *  window, and that the only real remedy is a message into that session, which
 *  is the steer verb rather than a new mechanism. So there is no cache
 *  invalidation here and there must not be one. */
export function stalenessNote(writers: DirectoryEntry[]): string | null {
  if (writers.length === 0) return null;
  const who = writers.length === 1
    ? `${holderName(writers[0])} is`
    : `${writers.length} sessions are`;
  return `${who} writing this tree right now, so what is on this page may already be `
    + `out of date. Reloading re-reads it. A session that has already read it has to be told.`;
}
