<!-- author: self; chat: 50cf71c1e6564676b908b6f33ddf9fe1 -->
Interaction contract for worktrees, branches and leases, decided 2026-08-28 after the operator rejected the shipped tree control.

**The worktree is the one visible noun.** The branch is named after it and never named separately. The lease is shown as status and never managed by hand. Exposing worktree + branch + lease as three controls was explicitly ruled out by the operator as 'giving them all of the tools... shapeless and confusing'.

**Nobody picks a worktree.** A chat that only reads stays on the trunk, read-only, forever. The first write creates a worktree named from the chat or ticket title, pins the chat, takes the lease and continues the same turn. This is branches-doc s14.1 (branch on write) and leases-doc s10.3 (new branch by default) with the missing half built: the creation becomes invisible instead of being a picker.

**Switching is demoted, not removed.** The menu states where you are; 'Move this chat to...' is a submenu; branch, lease, diff, merge and discard live one level down on the worktree's detail view.

**A direct operator message preempts the lease.** Consistent with leases s8 (authority is bound to a live human action). Sending the message IS the human action, so no Take over button; the holder's work is snapshotted to refs/wip/ first. An unattended run in the same position still queues.

Full rationale: docs/platform-redesign/worktree-as-the-place-2026-08-28.md