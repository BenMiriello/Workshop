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

/** The label. Short, because it sits on a toolbar that also carries the chat's
 *  name: the shared tree is named by its real ref, a pin by its slug. */
export function treeLabel(tree: ChatWorktree | null): string {
  if (!tree) return "Tree";
  if (tree.branch_slug) return tree.branch_slug;
  return tree.default_ref || "shared tree";
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
