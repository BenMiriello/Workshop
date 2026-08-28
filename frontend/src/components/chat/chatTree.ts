// The pure rules behind the chat's working-tree pill: what it is called, and
// what colour it is. Extracted so they can be executed (frontend/CLAUDE.md:
// there is no DOM harness, so the target of a test is the pure rule behind a
// behaviour), and read by `TreePill.tsx` and nothing else.
//
// FOUR STATUS KINDS, AND `danger` IS NOT ONE OF THEM, for exactly the reason
// `lib/holders.ts` gives: a preempted lease is simply the next generation held
// by somebody else, and painting an ordinary held tree red would fire on the
// healthy case every time. `danger` belongs to the take-over control, where an
// actual destruction is being proposed.
import { holderStatus, type DirectoryEntry, type HolderStatus } from "../../lib/holders";
import type { components } from "../../api/schema.gen";

// Generated from the backend's own OpenAPI schema, never hand-written.
export type ChatWorktree = components["schemas"]["ChatWorktree"];

/** How wide a run of hex has to be before it is an id rather than a name, and
 *  how much of it the pill keeps. Same rule and same numbers as the Branches
 *  page (`lib/branchesView.branchLabel`), because it is the same defect: a
 *  32-character machine slug is not a name and rendered whole it takes the
 *  whole toolbar. Duplicated as a THRESHOLD, not as a function, because this
 *  pill has a harder cap than a list row does. */
const HEX_ID_MIN = 24;
const HEX_ID_KEEP = 8;

/**
 * The label, and it is ALWAYS SHOWN, including on a phone.
 *
 * This is the whole job of the control (operator, 2026-08-28: an indicator has
 * to answer, at a glance, am I in a worktree or the shared tree and is anyone
 * else in it). It used to carry `chat-tb__picker-val--wide`, which is the class
 * that hides a picker's text under 640px because the icon already names the
 * value. That is true of the scope picker and false here: a branch icon says
 * "something about branches", not WHICH tree, so at 390px the pill answered
 * nothing.
 *
 * "Shared" rather than the ref's own name for the trunk, because the ref is
 * usually `main` and a toolbar reading `main` beside a conversation picker that
 * also says branch is the exact ambiguity this control exists to avoid. The
 * full name, with the ref, is the first row of the menu.
 */
export function treeLabel(tree: ChatWorktree | null): string {
  if (!tree) return "Tree";
  if (!tree.branch_slug) return "Shared";
  if (tree.display_name) return tree.display_name;
  const slug = tree.branch_slug;
  const m = /^(t-)?([0-9a-f]+)$/i.exec(slug.trim());
  if (!m) return slug;
  const [, prefix = "", hex] = m;
  if (hex.length < HEX_ID_MIN) return slug;
  return `${prefix}${hex.slice(0, HEX_ID_KEEP)}…`;
}

/** The pill's status kind, from the four this view can honestly reach.
 *
 *  `ok` you hold it, `running` somebody else does, `warn` they hold it and it
 *  has gone quiet, `stopped` nobody holds it -- which is the ordinary state of
 *  a chat between turns and is not a problem. The shared tree is `stopped` too:
 *  nothing is held there by design. */
export function treeStatus(
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
 * tree is yours right now, or somebody else is in it. Both are the answer to
 * "is anyone else in it", and both are rare.
 */
export function showsHolder(status: HolderStatus): boolean {
  return status.kind !== "stopped";
}
