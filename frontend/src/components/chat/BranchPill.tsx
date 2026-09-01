import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, GitMerge, Plus } from "lucide-react";
import {
  Button, Menu, MenuItem, MenuSeparator, Modal, Notice, Stack, StatusDot,
  shortHttpError,
} from "@workshop/ui";
import { branchLabel, branchName, branchStatus, showsHolder, type ChatWorktree } from "./chatBranch";
import { queryKeys } from "../../lib/queryKeys";

// WHICH BRANCH THIS CHAT IS ON.
//
// THE WORD IS BRANCH, and that is the change this file records.
// `threads-branches-and-pages-2026-08-29.md` §3: "A branch has exactly one
// worktree and always has... The interface says branch. The worktree is the
// directory it lives in, shown only in a detail view, never as a name."
//
// This control used to say worktree six times: a menu titled "Working tree", a
// trigger reading "Working tree: main", a row reading "Open the worktree", a
// modal offering "Work in a new worktree", and a cold label reading "Tree". The
// wire type is still `ChatWorktree` and the directory is still a worktree,
// because that IS the implementation; §3's point is that the implementation is
// not the vocabulary. `chatBranch.ts` is where the two meet.
//
// The shape is unchanged from `worktree-as-the-place-2026-08-28.md` §5, which
// §3 renames rather than replaces:
//
//   Row 1: a statement — the branch this chat is on, its state, who else is on
//   it. Not a control.
//   Then: "Move this chat to..." — a secondary action, one level down.
//
// Nobody picks a branch on the normal path. This pill names where you are.
//
// THERE IS NO THIRD ROW. It used to hold "Open the worktree", which called
// `nav("/chats")` — the unfiltered chat list, for every branch, with the
// comment above it claiming the list was "filtered to this branch". No such
// filter exists. §3 puts branch, lease, diff, merge and discard on the app's
// Work tab and its branch lens (tickets `130ef15e`, `427a86fe`), neither of
// which is built. A row that names a destination it does not go to is worse
// than an absent one, so it is deleted here rather than left pointing at
// nothing; it comes back when there is a detail view to point at.
//
// SHOWN ONLY WHERE IT CAN DO SOMETHING. `can_pin` is false for every chat with
// no project tree — a data chat, a freestanding agent — and this renders
// nothing at all rather than a permanently disabled control.
//
// WHAT IT DOES NOT DO: it does not claim the branch. A chat holds its lease for
// the length of a TURN, not for the conversation.
//
// NOT TO BE CONFUSED WITH `BranchPicker`, which sits immediately to its left.
// That one is a line of the CONVERSATION (ADR 0020) and this one is a line of
// the CODE. English calls both a branch; §3 spends the word here, so the other
// control drops the branch glyph and says "line" throughout.

const MAX_ROWS = 8;

async function readTree(threadId: string): Promise<ChatWorktree> {
  const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/tree`);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as ChatWorktree;
}

export default function BranchPill({ threadId }: { threadId: string }) {
  const qc = useQueryClient();
  const [openMove, setOpenMove] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const q = useQuery({
    queryKey: queryKeys.chat.tree(threadId),
    queryFn: () => readTree(threadId),
    staleTime: 30_000,
    retry: false,
  });
  const tree = q.data ?? null;

  const move = useMutation({
    mutationFn: async (body: { action: string; branch?: string | null }) => {
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/tree`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "x-workshop-client": "web" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      }
      return (await r.json()) as ChatWorktree;
    },
    onSuccess: (next) => {
      setErr(null);
      qc.setQueryData(queryKeys.chat.tree(threadId), next);
    },
    onError: (e) => setErr(String(e)),
  });

  const choose = useCallback((action: string, branch?: string) => {
    setErr(null);
    setOpenMove(false);
    move.mutate({ action, branch: branch ?? null });
  }, [move]);

  if (q.isError && !tree) return null;
  if (tree && !tree.can_pin) return null;

  const rows = (tree?.branches ?? []).slice(0, MAX_ROWS);
  // Directory is not polled here; branchStatus is fine with null holder and
  // returns `running / "Held"` whenever blocked_by is set, which is enough
  // for the pill's two-fact display.
  const status = branchStatus(tree, null, threadId);

  // Destinations for the Move modal: exclude the branch the chat is already on.
  const moveTargets = rows.filter((b) => b.branch_slug !== tree?.branch_slug);
  // Show the trunk option only when the chat is not already on it.
  const showTrunk = !!tree?.branch_slug;

  return (
    <>
      {/* THE TRIGGER ANSWERS TWO THINGS: which branch, and whether anybody is
          on it. Label always shown (an icon alone cannot name which). Dot only
          when the branch is held. */}
      <Menu
        label="Branch"
        trigger={
          <button
            className={`chat-tb__picker chat-tb__branch${tree?.branch_slug ? "" : " chat-tb__branch--shared"}`}
            title={tree?.detail || "Which branch this chat's turns run on"}
            aria-label={`Branch: ${branchLabel(tree)}`}
          >
            {tree?.branch_slug
              ? <GitBranch size={14} aria-hidden />
              : <GitMerge size={14} aria-hidden />}
            <span className="chat-tb__picker-val">{branchLabel(tree)}</span>
            {showsHolder(status) && (
              <StatusDot status={status.kind}>{null}</StatusDot>
            )}
          </button>
        }
      >
        {/* Row 1: a statement, not a control. The branch this chat is on, its
            state, and who is on it when someone is. Disabled so it is not
            treated as a move option. */}
        <MenuItem
          disabled
          icon={tree?.branch_slug
            ? <GitBranch size={14} aria-hidden />
            : <GitMerge size={14} aria-hidden />}
          onSelect={() => {}}
        >
          {branchLabel(tree)}
          {showsHolder(status) && (
            <StatusDot status={status.kind}>{status.label}</StatusDot>
          )}
        </MenuItem>

        <MenuSeparator />

        {/* Switching is demoted: one item that opens the full list. */}
        <MenuItem
          disabled={move.isPending}
          onSelect={() => setOpenMove(true)}
        >
          Move this chat to...
        </MenuItem>
      </Menu>

      {err && (
        <Notice tone="error" detail={err}>Couldn't move this chat.</Notice>
      )}

      {/* MOVE MODAL: the branches to move to. Only destinations — the current
          branch is not shown as an option.

          EVERY ROW IS NAMED BY `branchName`, THE SAME RULE THE PILL USES. This
          list used to print `branch_slug` raw, so on this install it drew three
          rows of 32-character hex inside the one control whose subject is a
          branch's name. */}
      <Modal
        open={openMove}
        onClose={() => setOpenMove(false)}
        title="Move this chat to..."
      >
        <Stack gap="sm">
          {showTrunk && (
            <Button variant="ghost" disabled={move.isPending} onClick={() => choose("release")}>
              <GitMerge size={16} aria-hidden />
              {tree?.default_ref || "Main"}
            </Button>
          )}
          {moveTargets.map((b) => (
            <Button
              key={b.branch_slug}
              variant="ghost"
              disabled={move.isPending}
              onClick={() => choose("join", b.branch_slug ?? undefined)}
              title={b.branch_slug ?? undefined}
            >
              <GitBranch size={16} aria-hidden />
              <span className="ws-truncate">{branchName(b)}</span>
            </Button>
          ))}
          {(tree?.branches?.length ?? 0) > MAX_ROWS && (
            <p className="ws-muted">{`Showing ${MAX_ROWS} of ${tree?.branches?.length}`}</p>
          )}
          <Button variant="ghost" disabled={move.isPending} onClick={() => choose("new_branch")}>
            <Plus size={16} aria-hidden />
            Work on a new branch
          </Button>
        </Stack>
      </Modal>
    </>
  );
}
