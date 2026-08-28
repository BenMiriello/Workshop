import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, GitMerge, Plus } from "lucide-react";
import {
  Menu, MenuItem, MenuLabel, MenuSeparator, Modal, Notice, Stack, StatusDot,
  shortHttpError,
} from "@workshop/ui";
import { holderFor } from "../../lib/holders";
import { showsHolder, treeLabel, treeStatus, type ChatWorktree } from "./chatTree";
import { queryKeys } from "../../lib/queryKeys";
import { useDirectory } from "../holders/useHolders";
import HolderControls from "../holders/HolderControls";

// WHICH TREE THIS CHAT WORKS IN, and the way to move it.
// `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` section
// 10: the ref is "inherited, shown, changeable", and section 14 puts the pill
// that shows it on the chat toolbar.
//
// IT IS NOT THE `BranchPicker` BESIDE IT. That one switches which line of the
// CONVERSATION you are reading (ADR 0020); this one switches which working tree
// the next turn runs in. Two different things that English calls a branch, so
// this control never says the bare word on its own: it says "Shared" or the
// branch's slug, under a "Where this chat works" heading.
//
// TWO FACTS AND NOTHING MORE (operator, 2026-08-28): am I in a worktree or the
// shared tree, and is anyone else in it. That is the label plus a dot, and the
// dot is there only when somebody IS in it. Everything else this control knows
// (the ref, the holder, the other branches, the way to cut one) is in the menu,
// one tap away, which is where you are choosing rather than glancing.
//
// SHOWN ONLY WHERE IT CAN DO SOMETHING. `can_pin` is false for every chat with
// no project tree of its own -- a data chat, a freestanding agent -- and this
// renders nothing at all rather than a control that is permanently disabled.
//
// WHAT IT DOES NOT DO: it does not claim the tree. A chat holds its worktree
// lease for the length of a TURN, not for the length of the conversation
// (section 7: sessions are disposable, trees are not), so opening this menu
// takes nothing from anyone and an idle chat correctly shows the tree as free.

// A handful of branches is the normal state. Past this the menu stops being a
// picker, and the count plus the way to the project page is the overflow state.
const MAX_ROWS = 8;

async function readTree(threadId: string): Promise<ChatWorktree> {
  const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/tree`);
  if (!r.ok) {
    throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return (await r.json()) as ChatWorktree;
}

export default function TreePill({ threadId }: { threadId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const q = useQuery({
    queryKey: queryKeys.chat.tree(threadId),
    queryFn: () => readTree(threadId),
    // A pin changes only when somebody changes it, and the holder changes when
    // a turn starts or ends. The dialog below reads presence at its own 15s
    // cadence, so this does not need a timer of its own.
    staleTime: 30_000,
    retry: false,
  });
  const tree = q.data ?? null;

  // Only while the menu's dialog is open: a chat that never looks at its tree
  // must not add a poll to every conversation on screen.
  const directory = useDirectory({ enabled: open, subjectKind: "worktree" });
  const holder = directory.entries
    ? holderFor(directory.entries, tree?.subject_id ?? null)
    : null;

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
    move.mutate({ action, branch: branch ?? null });
  }, [move]);

  if (q.isError && !tree) return null;
  // Nothing to pin: a data chat or a freestanding agent has no branch space,
  // so there is no control rather than a disabled one.
  if (tree && !tree.can_pin) return null;

  const rows = (tree?.branches ?? []).slice(0, MAX_ROWS);
  const status = treeStatus(tree, holder, threadId);

  return (
    <>
      {/* THE TRIGGER ANSWERS TWO THINGS AND NOTHING ELSE: which tree, and
          whether anybody is in it. The label is always shown, at every width
          (see `treeLabel`), because an icon on its own cannot say which. The
          dot appears only when the tree is held, by you or by somebody else, so
          it is a signal rather than a permanent grey mark on every toolbar in
          the app. */}
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
        <MenuLabel>Where this chat works</MenuLabel>
        {/* The trunk is always first and is always reachable: going back to the
            shared tree must never depend on a branch record still existing. */}
        <MenuItem
          icon={<GitMerge size={14} aria-hidden />}
          active={!tree?.branch_slug}
          disabled={move.isPending}
          title={`The project's own tree${tree?.default_ref ? ` (${tree.default_ref})` : ""}. Shared, so nothing is held there.`}
          onSelect={() => choose("release")}
        >
          {`Shared tree${tree?.default_ref ? ` · ${tree.default_ref}` : ""}`}
        </MenuItem>

        <MenuSeparator />
        <MenuLabel>Branches</MenuLabel>
        {/* All four list states (frontend/CLAUDE.md): cold, error, empty, and
            the overflow line under the rows. */}
        {!tree && !q.isError ? (
          <MenuItem onSelect={() => {}} disabled>Reading branches</MenuItem>
        ) : q.isError ? (
          <MenuItem onSelect={() => { void q.refetch(); }} title={String(q.error)}>
            Couldn't read the branches. Retry
          </MenuItem>
        ) : rows.length === 0 ? (
          <MenuItem
            onSelect={() => {}}
            disabled
            title="Cut one below. A branch is a working tree of its own, so this chat's writes stay off the shared one."
          >
            No branches are open on this project
          </MenuItem>
        ) : (
          rows.map((b) => (
            <MenuItem
              key={b.branch_slug}
              icon={<GitBranch size={14} aria-hidden />}
              active={b.branch_slug === tree?.branch_slug}
              disabled={move.isPending}
              title={b.dir}
              onSelect={() => choose("join", b.branch_slug)}
            >
              <span className="ws-truncate">{b.branch_slug}</span>
              {b.branch_slug === tree?.branch_slug && (
                <StatusDot status={status.kind}>{status.label}</StatusDot>
              )}
            </MenuItem>
          ))
        )}
        {(tree?.branches?.length ?? 0) > MAX_ROWS && (
          <MenuLabel>{`Showing ${MAX_ROWS} of ${tree?.branches?.length}`}</MenuLabel>
        )}

        <MenuSeparator />
        {/* SECTION 10.3'S DEFAULT, and the reason it is a plain item rather than
            a dialog: a branch merges as one unit, so a new one is the SAFE
            choice and joining is the one that couples this work to another
            ticket's fate. Making the safe act the cheap act is the whole rule. */}
        <MenuItem
          icon={<Plus size={14} aria-hidden />}
          disabled={move.isPending}
          onSelect={() => choose("new_branch")}
        >
          Work in a new branch
        </MenuItem>
        {tree?.blocked_by && (
          <MenuItem
            icon={<GitBranch size={14} aria-hidden />}
            onSelect={() => setOpen(true)}
          >
            Somebody is on this tree
          </MenuItem>
        )}
      </Menu>

      {err && (
        <Notice tone="error" detail={err}>Couldn't move this chat.</Notice>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={tree?.branch_slug ? `Who is on ${tree.branch_slug}` : "Who is on this tree"}
      >
        <Stack gap="sm">
          {holder ? (
            <HolderControls
              entry={holder}
              // A CHAT IS A SESSION, so unlike a page it can be handed the
              // lease: `selfSessionId` is the thread id, which is exactly the
              // holder id `chat_worktree` takes the lease under.
              selfSessionId={threadId}
              worktreeId={tree?.subject_id ?? null}
              onChanged={() => {
                directory.reload();
                void q.refetch();
              }}
            />
          ) : (
            <p className="ws-muted">
              The lease says this tree is held, but nothing is beating on it. It
              expires on its own; nothing needs stopping.
            </p>
          )}
        </Stack>
      </Modal>
    </>
  );
}
