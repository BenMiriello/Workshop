import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, GitBranch, GitMerge } from "lucide-react";
import {
  Button, Menu, MenuItem, MenuLabel, MenuSeparator, Modal, Stack, StatusDot,
} from "@workshop/ui";
import {
  branchStatus, holderFor, holderLine,
  type BranchRecord, type DirectoryEntry,
} from "../../lib/holders";
import { branchChipLabel } from "../shell/pageChrome";
import type { AppRefRead } from "./useHolders";
import HolderControls from "./HolderControls";

// WHICH VERSION AM I LOOKING AT
// (branches-worktrees-and-environments-2026-08-20.md section 9.1).
//
// IT LIVES IN THE SHELL'S CHROME NOW, not in the page (moved 2026-09-01 on the
// operator's instruction). It was rendered into `AppTabPage`'s `PageHeader`
// actions, which put a branch picker inside the BODY of every single app tab
// page: the same control re-drawn per page, on a line that belongs to the
// page's own actions, in a place nobody asked for it. His words: "it's just on
// every page inside the page, which makes no sense. It should be in the
// toolbar... by the title."
//
// So it is mounted twice now, once per viewport's chrome, and both mounts read
// ONE state that `useShell` holds:
//
//   * DESKTOP -- the page column's title line, right-aligned beside the place
//     name (navigation-shell-2026-08-20.md section 3.2: "a ref pill sits
//     right-aligned on the same line where the place has a ref"). See
//     shell/PageColumn.tsx.
//   * PHONE -- bar one, between the title and the avatar, truncated so the bar
//     never grows and never wraps. See shell/MobileBars.tsx.
//
// And it is drawn ONLY where there is a branch to name. That is the surviving
// kernel of design doc section 8.2, and the rule is `branchSubject` in
// shell/pageChrome.ts.
//
// EVERYTHING IT SAYS SAYS "BRANCH" (threads-branches-and-pages-2026-08-29.md
// section 3: "the interface says branch"). Not "ref", which is the git term and
// is what this read the day it lived in a page header, and not "worktree",
// which is the directory a branch lives in and is never a name.
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
  state, projectSlug, selfSessionId, className, labelMax = 14, tabIndex,
}: {
  state: AppRefRead;
  projectSlug?: string | null;
  /** The session this surface is, when it is one. A page is not one, so the
   *  dialog it opens offers queue and steer but not a take-over. */
  selfSessionId?: string | null;
  /** Which chrome is holding it. Carries the width cap and, in the page column,
   *  the cap on the MENU: that column is `overflow: hidden` (it has to be, it
   *  slides itself out) so a list wider than the column is sliced off. */
  className?: string;
  /** How many characters of the branch name fit here. The bar and the column
   *  have different room; neither of them grows. */
  labelMax?: number;
  /** -1 while the chrome around it is closed or slid out of view. The page
   *  column stays MOUNTED when collapsed so it has something to animate out
   *  of, which means its contents have to leave the tab order by hand. */
  tabIndex?: number;
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
        label="Branch"
        className={className}
        trigger={
          <Button
            size="sm"
            tabIndex={tabIndex}
            title={ref ? `On branch ${ref}` : "This app's branch could not be read"}
            // The visible name is truncated to fit the chrome, so the full one
            // is said here. Every row of the menu carries it unabbreviated too.
            aria-label={ref ? `Branch: ${ref}` : "Branch: none recorded"}
          >
            {/* NO `+N` COUNT ON THE TRIGGER. It used to carry one, when this
                lived in a roomy page header. In the shell's chrome it cost
                about a quarter of a 252px column and a third of the room the
                phone bar has for the name, and the thing it displaced is the
                only part that identifies which branch you are on. The count
                is in the menu, where the rows it counts also are. */}
            <GitBranch size={13} aria-hidden />
            <span className="ws-refpill__name">{branchChipLabel(ref, labelMax)}</span>
            <ChevronDown size={12} aria-hidden className="ws-refpill__caret" />
          </Button>
        }
      >
        <MenuLabel>Branch</MenuLabel>
        {/* The trunk is always first, and it is where the app pages read from.
            It is `active`, not `checked`: this is where the app currently is,
            not a choice that was made. */}
        <MenuItem
          icon={<GitBranch size={14} aria-hidden />}
          active
          title={ref ? `This app's tree is on ${ref}` : "This app's branch could not be read"}
          onSelect={() => {}}
        >
          {ref ?? "no branch recorded"}
        </MenuItem>

        <MenuSeparator />
        <MenuLabel>{records && records.length > 0 ? `Other branches (${records.length})` : "Other branches"}</MenuLabel>
        {cold && !err ? (
          <MenuItem onSelect={() => {}} disabled>Reading branches</MenuItem>
        ) : err && cold ? (
          <MenuItem onSelect={() => { state.reload(); }} title={err}>
            Couldn't read the branches. Retry
          </MenuItem>
        ) : rows.length === 0 ? (
          <MenuItem onSelect={() => {}} disabled title="A branch appears here when a ticket or a chat opens one on this app.">
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
