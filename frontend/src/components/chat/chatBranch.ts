// The pure rules behind the chat's BRANCH pill: what a branch is called, and
// what colour it is. Extracted so they can be executed (frontend/CLAUDE.md:
// there is no DOM harness, so the target of a test is the pure rule behind a
// behaviour), and read by `BranchPill.tsx` and nothing else.
//
// THE WORD IS BRANCH. `threads-branches-and-pages-2026-08-29.md` §3: "A branch
// has exactly one worktree and always has... Two words for one thing bought
// nothing. The interface says branch. The worktree is the directory it lives
// in, shown only in a detail view, never as a name."
//
// So the wire type is still `ChatWorktree` and the directory is still a
// worktree, because that is what the implementation is. Nothing a person reads
// says so. This module and the pill above it are where the two vocabularies
// meet, which is why the translation lives here and not in six call sites.
//
// FOUR STATUS KINDS, AND `danger` IS NOT ONE OF THEM, for exactly the reason
// `lib/holders.ts` gives: a preempted lease is simply the next generation held
// by somebody else, and painting an ordinary held branch red would fire on the
// healthy case every time. `danger` belongs to the take-over control, where an
// actual destruction is being proposed.
import { holderStatus, type DirectoryEntry, type HolderStatus } from "../../lib/holders";
import type { components } from "../../api/schema.gen";

// Generated from the backend's own OpenAPI schema, never hand-written.
export type ChatWorktree = components["schemas"]["ChatWorktree"];

/** One branch as the wire carries it, which is the shape of a row in the
 *  pill's move list as well as of the chat's own branch. */
export type BranchRef = { branch_slug?: string | null; display_name?: string | null };

/** How wide a run of hex has to be before it is an id rather than a name, and
 *  how much of it we keep.
 *
 *  §3 says a branch is named after its work and this rule goes away with the
 *  last machine-named branch. It is still here because it still fires: six of
 *  the six branches this install serves come back with `display_name: null` and
 *  three of them are named `<32 hex>`. Until the legacy trees are renamed or
 *  reaped (§3's housekeeping, ticket `1d779456`) an id has to be survivable on
 *  a 390px bar, and it has to be survivable in the LIST too, which is where it
 *  was not: the move list rendered `branch_slug` raw and drew three 32-character
 *  hex rows. That is §3's own named defect ("that is why branches showed as
 *  hex") sitting inside the control §3 is about. */
const HEX_ID_MIN = 24;
const HEX_ID_KEEP = 8;

/**
 * The readable name of ONE branch. The declared name wins; a machine id is
 * shortened; anything else is what it says it is.
 *
 * ONE FUNCTION, BOTH PLACES. The pill and the move list are the same question
 * asked about a different branch, so they may not answer it differently.
 */
export function branchName(ref: BranchRef | null): string {
  if (!ref) return "";
  if (ref.display_name) return ref.display_name;
  const slug = (ref.branch_slug || "").trim();
  if (!slug) return "";
  const m = /^(t-)?([0-9a-f]+)$/i.exec(slug);
  if (!m) return slug;
  const [, prefix = "", hex] = m;
  if (hex.length < HEX_ID_MIN) return slug;
  return `${prefix}${hex.slice(0, HEX_ID_KEEP)}…`;
}

/**
 * The pill's label, and it is ALWAYS SHOWN, including on a phone.
 *
 * This is the whole job of the control (operator, 2026-08-28: an indicator has
 * to answer, at a glance, which branch am I on and is anyone else in it). It
 * used to carry `chat-tb__picker-val--wide`, which is the class that hides a
 * picker's text under 640px because the icon already names the value. That is
 * true of the scope picker and false here: a branch glyph says "something about
 * branches", not WHICH one, so at 390px the pill answered nothing.
 *
 * The trunk is named by its own ref ("main", "develop", whatever the project
 * chose) rather than "Shared" -- that word was invented and doubled the name
 * ("Shared tree · main").
 */
export function branchLabel(tree: ChatWorktree | null): string {
  if (!tree) return "Branch";
  if (!tree.branch_slug) return tree.default_ref || "Main";
  return branchName(tree) || tree.default_ref || "Main";
}

/** The pill's status kind, from the four this view can honestly reach.
 *
 *  `ok` you hold it, `running` somebody else does, `warn` they hold it and it
 *  has gone quiet, `stopped` nobody holds it -- which is the ordinary state of
 *  a chat between turns and is not a problem. The trunk is `stopped` too:
 *  nothing is held there by design. */
export function branchStatus(
  tree: ChatWorktree | null, holder: DirectoryEntry | null, threadId: string,
): HolderStatus {
  if (!tree || !tree.branch_slug) return { kind: "stopped", label: "Shared" };
  if (tree.held) return { kind: "ok", label: "Yours" };
  if (tree.blocked_by) {
    if (holder) {
      const s = holderStatus(holder, threadId);
      if (s.kind === "warn" || s.kind === "running") return s;
    }
    return { kind: "running", label: "Held" };
  }
  return { kind: "stopped", label: "Free" };
}

/**
 * Whether the pill shows a dot at all.
 *
 * A CONSTANT IS NOT A SIGNAL (frontend/CLAUDE.md). Every chat that is not
 * mid-turn shows `stopped` here, and a grey dot on every toolbar in the app
 * says nothing while costing the label its width. Section 14 of the leases doc
 * asks for the same thing in the other direction: the ref pill carries "the
 * holder's avatar WHEN SOMEONE ELSE HOLDS IT".
 *
 * So the dot appears for exactly the two states the operator asked about: this
 * branch is yours right now, or somebody else is on it. Both are the answer to
 * "is anyone else in it", and both are rare.
 */
export function showsHolder(status: HolderStatus): boolean {
  return status.kind !== "stopped";
}
