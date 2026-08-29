import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, GitMerge, Plus } from "lucide-react";
import {
  Button, Menu, MenuItem, MenuSeparator, Modal, Notice, Stack, StatusDot,
  shortHttpError,
} from "@workshop/ui";
import { showsHolder, treeLabel, treeStatus, type ChatWorktree } from "./chatTree";
import { queryKeys } from "../../lib/queryKeys";

// WHICH WORKTREE THIS CHAT IS IN.
// `docs/platform-redesign/worktree-as-the-place-2026-08-28.md` §5:
//
//   Row 1: a statement — the worktree this chat is in, its state, who else is
//   in it. Not a control.
//   Then: "Move this chat to..." — a secondary action, one level down.
//   Then: "Open the worktree" — the Branches page, where branch, lease, diff,
//   merge and discard live.
//
// Nobody picks a worktree on the normal path. This pill names where you are.
//
// SHOWN ONLY WHERE IT CAN DO SOMETHING. `can_pin` is false for every chat with
// no project tree — a data chat, a freestanding agent — and this renders
// nothing at all rather than a permanently disabled control.
//
// WHAT IT DOES NOT DO: it does not claim the tree. A chat holds its worktree
// lease for the length of a TURN, not for the conversation.

const MAX_ROWS = 8;

async function readTree(threadId: string): Promise<ChatWorktree> {
  const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/tree`);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as ChatWorktree;
}

export default function TreePill({ threadId }: { threadId: string }) {
  const nav = useNavigate();
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
  // Directory is not polled here; treeStatus is fine with null holder and
  // returns `running / "Held"` whenever blocked_by is set, which is enough
  // for the pill's two-fact display.
  const status = treeStatus(tree, null, threadId);

  // Destinations for the Move modal: exclude the worktree the chat is already in.
  const moveTargets = rows.filter((b) => b.branch_slug !== tree?.branch_slug);
  // Show the trunk option only when the chat is not already on it.
  const showTrunk = !!tree?.branch_slug;

  return (
    <>
      {/* THE TRIGGER ANSWERS TWO THINGS: which worktree, and whether anybody
          is in it. Label always shown (an icon alone cannot name which). Dot
          only when the tree is held. */}
      <Menu
        label="Working tree"
        trigger={
          <button
            className={`chat-tb__picker chat-tb__tree${tree?.branch_slug ? "" : " chat-tb__tree--shared"}`}
            title={tree?.detail || "Which working tree this chat's turns run in"}
            aria-label={`Working tree: ${treeLabel(tree)}`}
          >
            {tree?.branch_slug
              ? <GitBranch size={14} aria-hidden />
              : <GitMerge size={14} aria-hidden />}
            <span className="chat-tb__picker-val">{treeLabel(tree)}</span>
            {showsHolder(status) && (
              <StatusDot status={status.kind}>{null}</StatusDot>
            )}
          </button>
        }
      >
        {/* Row 1: a statement, not a control. The worktree this chat is in,
            its state, and who is in it when someone is. Disabled so it is
            not treated as a move option. */}
        <MenuItem
          disabled
          icon={tree?.branch_slug
            ? <GitBranch size={14} aria-hidden />
            : <GitMerge size={14} aria-hidden />}
          onSelect={() => {}}
        >
          {treeLabel(tree)}
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

        {/* Only when in a worktree (not trunk): the detail view where branch,
            lease, diff, merge and discard live. */}
        {tree?.branch_slug && (
          <MenuItem
            icon={<GitBranch size={14} aria-hidden />}
            onSelect={() => nav("/chats")}
          >
            Open the worktree
          </MenuItem>
        )}
      </Menu>

      {err && (
        <Notice tone="error" detail={err}>Couldn't move this chat.</Notice>
      )}

      {/* MOVE MODAL: the list of worktrees to move to. Only destinations —
          the current worktree is not shown as an option. */}
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
              onClick={() => choose("join", b.branch_slug)}
            >
              <GitBranch size={16} aria-hidden />
              <span className="ws-truncate">{b.branch_slug}</span>
            </Button>
          ))}
          {(tree?.branches?.length ?? 0) > MAX_ROWS && (
            <p className="ws-muted">{`Showing ${MAX_ROWS} of ${tree?.branches?.length}`}</p>
          )}
          <Button variant="ghost" disabled={move.isPending} onClick={() => choose("new_branch")}>
            <Plus size={16} aria-hidden />
            Work in a new worktree
          </Button>
        </Stack>
      </Modal>
    </>
  );
}
