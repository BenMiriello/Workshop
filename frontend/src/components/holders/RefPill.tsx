import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, GitMerge } from "lucide-react";
import {
  Button, Menu, MenuItem, MenuLabel, MenuSeparator, Modal, Stack, StatusDot,
} from "@workshop/ui";
import {
  branchStatus, holderFor, holderLine, refPillLabel,
  type BranchRecord, type DirectoryEntry,
} from "../../lib/holders";
import type { AppRefRead } from "./useHolders";
import HolderControls from "./HolderControls";

// WHICH VERSION AM I LOOKING AT, on the app's own header
// (branches-worktrees-and-environments-2026-08-20.md section 9.1).
//
// It goes in the header that is already there (`AppTabPage`'s `PageHeader`
// actions) rather than in a new strip of chrome. A phone has no room for a
// second bar, and the last time one was added the operator measured 290px of
// duplicated chrome above a file list.
//
// WHAT IT DOES NOT DO, and this is deliberate. Section 9.1 wants the pill to
// SELECT which working tree the page is looking at. Nothing in the tree reads a
// per-surface ref yet, so a menu that appeared to switch would be a control
// with nothing behind it. Choosing a branch here opens what CAN be acted on
// today: who holds it, and the verbs against that holder. The project page is
// where a branch is run, merged or discarded, and the footer links there.

// A handful of branch trees is the normal state. Past this the menu stops being
// a picker; the count and the link to the project page are the overflow state.
const MAX_ROWS = 8;

export default function RefPill({
  state, projectSlug, selfSessionId,
}: {
  state: AppRefRead;
  projectSlug?: string | null;
  /** The session this surface is, when it is one. A page is not one, so the
   *  dialog it opens offers queue and steer but not a take-over. */
  selfSessionId?: string | null;
}) {
  const [open, setOpen] = useState<{ record: BranchRecord; holder: DirectoryEntry } | null>(null);
  const navigate = useNavigate();
  const { ref, records, entries, err } = state;
  const cold = records === null;
  const rows = (records ?? []).slice(0, MAX_ROWS);
  const live = entries ?? [];

  return (
    <>
      <Menu
        label="Ref"
        trigger={
          <Button size="sm" title="Which ref this app is on">
            <GitBranch aria-hidden />
            {refPillLabel(ref)}
            {records && records.length > 0 && (
              <span className="ws-muted">{` +${records.length}`}</span>
            )}
          </Button>
        }
      >
        <MenuLabel>Ref</MenuLabel>
        {/* The trunk is always first, and it is where the app pages read from.
            It is `active`, not `checked`: this is where the app currently is,
            not a choice that was made. */}
        <MenuItem
          icon={<GitBranch size={14} aria-hidden />}
          active
          title={ref ? `The app's tree is on ${ref}` : "The ref of this app's tree could not be read"}
          onSelect={() => {}}
        >
          {refPillLabel(ref)}
        </MenuItem>

        <MenuSeparator />
        <MenuLabel>Branches</MenuLabel>
        {cold && !err ? (
          <MenuItem onSelect={() => {}} disabled>Reading branches</MenuItem>
        ) : err && cold ? (
          <MenuItem onSelect={() => { state.reload(); }} title={err}>
            Couldn't read the branches. Retry
          </MenuItem>
        ) : rows.length === 0 ? (
          <MenuItem onSelect={() => {}} disabled title="A branch appears here when a ticket or a chat opens a worktree on this app.">
            No branches are open on this app
          </MenuItem>
        ) : (
          rows.map((r) => {
            const holder = holderFor(live, r.id);
            const st = branchStatus(r, holder, selfSessionId);
            return (
              <MenuItem
                key={r.id}
                icon={<GitBranch size={14} aria-hidden />}
                title={holder ? holderLine(holder) : "Nobody is on this branch."}
                onSelect={() => { if (holder) setOpen({ record: r, holder }); }}
              >
                <span className="ws-truncate">{r.branch}</span>
                <StatusDot status={st.kind}>{st.label}</StatusDot>
              </MenuItem>
            );
          })
        )}
        {(records?.length ?? 0) > MAX_ROWS && (
          <MenuLabel>{`Showing ${MAX_ROWS} of ${records?.length}`}</MenuLabel>
        )}
        {projectSlug && (
          <>
            <MenuSeparator />
            {/* A route, not a nested <Link>: a MenuItem IS a button, and an
                anchor inside one is invalid and unreachable by keyboard. */}
            <MenuItem
              icon={<GitMerge size={14} aria-hidden />}
              onSelect={() => navigate(`/projects/${projectSlug}`)}
            >
              Run, merge or discard a branch
            </MenuItem>
          </>
        )}
      </Menu>

      <Modal
        open={!!open}
        onClose={() => setOpen(null)}
        title={open ? `Who is on ${open.record.branch}` : ""}
      >
        {open && (
          <Stack gap="sm">
            <HolderControls
              entry={open.holder}
              selfSessionId={selfSessionId}
              worktreeId={open.record.id}
              onChanged={state.reload}
            />
          </Stack>
        )}
      </Modal>
    </>
  );
}
