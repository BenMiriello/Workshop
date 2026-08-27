// FileDrill: the phone shape of `FileTree`. One level on screen, a folder tap
// descends into it, and Up walks back out.
//
// Specified in docs/platform-redesign/navigation-shell-2026-08-20.md section
// 8.6: "On the Files page the file tree is the page content and drills in
// place, with Up walking the tree. It does not take over the tab bar." A rail
// stacked above a file is what this replaces: on a narrow screen the two-pane
// layout puts a scrolling tree directly above the file's own scroll, so the
// picker and the thing it picked compete for the same page.
//
// SAME ADAPTER AS `FileTree`, deliberately. A surface that shows the tree in a
// rail on a desktop and drills it on a phone writes ONE adapter and branches on
// width; nothing about the data is said twice. The level store is the same
// three transitions too (`levelLoading` / `levelReady` / `levelFailed` in
// fileTree.ts), so there is no second loader to keep in step with the first.
//
// UP IS STRUCTURAL, NOT BACK (section 8.1). The way out of a level is the trail
// this component is handed, not browser history, and AT THE ROOT IT LEAVES THE
// DIRECTORY PAGE rather than doing nothing: `drillUpIntent` decides that, and
// the page it leaves for is the caller's answer (`onExit`). The trail lives in
// the CALLER so that opening a file and coming back up returns to the level the
// file is in, which is the whole point of Up. `drillTrail` / `drillInto` /
// `drillUpIntent` in fileTree.ts are those rules, pure and tested.
//
// THE GEOMETRY IS THE MOCKUP'S, NOT PROSE (shell-mockup-spec-2026-08-22.md
// section 4, extracted from the mockup this surface is measured against):
//
//   - The Up bar is its OWN 40px full-width row above the list, and the WHOLE
//     bar is the tap target. A 13px arrow plus the word "Up" in `--accent`, then
//     the trail in `--faint`, both mono 12px, segments joined with " / ". No
//     segment is tappable on its own.
//   - Rows are 52px, FULL BLEED, `padding: 0 14px`, `gap: 11px`, and have ZERO
//     indentation. A phone list does not indent; depth is expressed by drilling,
//     which is the entire point of the pattern.
//   - A folder is the SANS face with a child count and a chevron; a file is the
//     MONO face with a relative time and no chevron. That face difference is how
//     the two are told apart here, so it is not decoration.
//
// All styling is in components.css under `.ws-drill`; no colors here.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowUp, ChevronRight, EyeOff, MoreHorizontal } from "lucide-react";
import { cx } from "./cx";
import { Button } from "./controls";
import { Skeleton, StateBlock } from "./feedback";
import { fileTreeIconFor } from "./FileTree";
import {
  drillCursor,
  drillInto,
  drillParent,
  drillTrail,
  drillTrailText,
  drillUpIntent,
  levelFailed,
  levelForget,
  levelLoading,
  levelReady,
  FILE_TREE_MAX_ROWS_PER_LEVEL,
  type FileDrillTrail,
  type FileTreeAdapter,
  type FileTreeLevels,
  type FileTreeNode,
} from "./fileTree";

export type FileDrillProps = {
  /** The same seam `FileTree` takes. Keep it stable (`useMemo`): a new adapter
   *  object drops every level this component has loaded and reads again. */
  adapter: FileTreeAdapter;
  /** Root first, the level being listed last. Build it with `drillTrail`. */
  trail: FileDrillTrail;
  /** A new trail, already computed: descending and Up both report through
   *  here, so the caller only has to store what it is given. */
  onTrail: (trail: FileDrillTrail) => void;
  /** Up AT THE ROOT leaves the directory page entirely (section 4.1). What
   *  leaving means is the page's own business and this component cannot know
   *  it, so it is reported here instead of being swallowed. Without it the bar
   *  is not drawn at the root at all, because a control that does nothing is
   *  worse than an absent one. */
  onExit?: () => void;
  /** The selected node's id, if the caller tracks one. Controlled. */
  selectedId?: string | null;
  /** A leaf was chosen. A branch is a level here, not a destination, so it
   *  descends instead and never reports as a selection. */
  onSelect?: (node: FileTreeNode) => void;
  /** Rows drawn before the rest becomes one overflow line. */
  maxRows?: number;
  /** The empty state at the ROOT. A level below it says which folder is empty,
   *  since naming it is the useful half. The hint is instructional either way
   *  (rule 9): it says how to put something here. */
  emptyTitle?: string;
  emptyHint?: ReactNode;
  /** Names the list for assistive tech: "App files", "Memory". */
  label?: string;
  className?: string;
};

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function FileDrill({
  adapter,
  trail,
  onTrail,
  onExit,
  selectedId,
  onSelect,
  maxRows = FILE_TREE_MAX_ROWS_PER_LEVEL,
  emptyTitle = "Nothing here",
  emptyHint = "Add something at this location and it shows up here.",
  label = "Files",
  className,
}: FileDrillProps) {
  const [levels, setLevels] = useState<FileTreeLevels>({});
  // Bumped whenever the adapter changes, so a level still in flight from the
  // old adapter cannot land in the new list.
  const genRef = useRef(0);

  // A trail must start at the root and must not be empty, so a caller that has
  // not built one yet still gets the root level rather than a blank screen.
  const at = useMemo<FileDrillTrail>(
    () => (trail.length > 0 ? trail : drillTrail(adapter.root)),
    [trail, adapter.root],
  );
  const cursor = drillCursor(at);
  const parent = drillParent(at);
  const state = levels[cursor.id];

  const load = useCallback(
    (node: FileTreeNode) => {
      const gen = genRef.current;
      setLevels((prev) => levelLoading(prev, node.id));
      adapter.children(node).then(
        (level) => {
          if (gen === genRef.current) setLevels((prev) => levelReady(prev, node.id, level));
        },
        (e: unknown) => {
          if (gen === genRef.current) setLevels((prev) => levelFailed(prev, node.id, messageOf(e)));
        },
      );
    },
    [adapter],
  );

  useEffect(() => {
    genRef.current += 1;
    setLevels({});
  }, [adapter]);

  // The level the trail points at, fetched once. Walking Up to a level that is
  // already loaded shows it immediately; the levels above stay in the store for
  // exactly that reason.
  useEffect(() => {
    if (!levels[cursor.id]) load(cursor);
  }, [cursor, levels, load]);

  const retry = useCallback(() => {
    setLevels((prev) => levelForget(prev, cursor.id));
  }, [cursor.id]);

  // ONE BAR, ONE TARGET. Not a button inside a header and not a breadcrumb: the
  // 40px row IS the control, and the trail beside "Up" is a label rather than a
  // set of links, so there is exactly one thing to hit on a phone.
  const up = drillUpIntent(at);
  const trailBar = up.type === "level" || onExit ? (
    <button
      type="button"
      className="ws-drill__up"
      onClick={() => (up.type === "level" ? onTrail(up.trail) : onExit?.())}
      aria-label={parent ? `Up to ${parent.label}` : `Up out of ${cursor.label}`}
    >
      <span className="ws-drill__up-go">
        <ArrowUp aria-hidden /> Up
      </span>
      <span className="ws-drill__up-trail">{drillTrailText(at)}</span>
    </button>
  ) : null;

  if (!state || state.status === "loading") {
    return (
      <div className={cx("ws-drill", className)} aria-busy="true" aria-label={label}>
        {trailBar}
        <div className="ws-drill__level">
          {["70%", "55%", "62%", "40%"].map((w) => (
            <div key={w} className="ws-drill__row ws-drill__row--pending">
              <Skeleton width={w} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (state.status === "error") {
    // The level IS the page here, so a failed level leaves nothing on screen:
    // a region, not a line (frontend/CLAUDE.md, "A region or a line"). The
    // trail stays above it, so Up still works out of a folder that will not
    // load.
    return (
      <div className={cx("ws-drill", className)}>
        {trailBar}
        <div className="ws-drill__block">
          <StateBlock
            variant="error"
            title={`Couldn't load ${cursor.label}`}
            hint={state.message}
            action={<Button size="sm" onClick={retry}>Retry</Button>}
          />
        </div>
      </div>
    );
  }

  if (state.nodes.length === 0) {
    return (
      <div className={cx("ws-drill", className)}>
        {trailBar}
        <div className="ws-drill__block">
          <StateBlock
            title={parent ? `Nothing in ${cursor.label}` : emptyTitle}
            hint={emptyHint}
          />
        </div>
      </div>
    );
  }

  const shown = maxRows > 0 ? state.nodes.slice(0, maxRows) : state.nodes;
  const hidden = state.nodes.length - shown.length;

  return (
    <div className={cx("ws-drill", className)}>
      {trailBar}
      <div className="ws-drill__level" aria-label={label} role="group">
        {shown.map((node, i) => {
          const branch = node.kind === "branch";
          const Icon = fileTreeIconFor(node, false);
          const selected = node.id === selectedId;
          // The same rule the desktop tree uses (`flattenTree`): a header when
          // the group CHANGES, so a level with one group or none draws none.
          // Memory's root is three different address kinds on a phone too.
          const head = node.group && (i === 0 || node.group !== shown[i - 1].group)
            ? <div key={`${node.group}::group`} className="ws-drill__group" role="presentation">{node.group}</div>
            : null;
          const row = (
            <button
              key={node.id}
              type="button"
              className={cx(
                "ws-drill__row",
                branch ? "ws-drill__row--folder" : "ws-drill__row--file",
                selected && "is-selected",
              )}
              aria-current={selected ? "true" : undefined}
              onClick={() => (branch ? onTrail(drillInto(at, node)) : onSelect?.(node))}
            >
              <Icon className="ws-drill__icon" aria-hidden />
              <span className="ws-drill__label">{node.label}</span>
              {node.detail ? <span className="ws-drill__meta">{node.detail}</span> : null}
              {branch && <ChevronRight className="ws-drill__into" aria-hidden />}
            </button>
          );
          return head ? [head, row] : row;
        })}
      </div>
      {hidden > 0 && (
        <p className="ws-drill__aside">
          <MoreHorizontal aria-hidden />
          {hidden} more not shown. Open a narrower folder to see them.
        </p>
      )}
      {state.filtered && state.filtered.length > 0 && (
        <p className="ws-drill__aside">
          <EyeOff aria-hidden />
          Hidden: {state.filtered.join(", ")}
        </p>
      )}
    </div>
  );
}
