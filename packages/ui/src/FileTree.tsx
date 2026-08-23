// FileTree: one tree navigator for the whole system, loading each level only
// when it is opened.
//
// Adopted in docs/platform-redesign/file-viewer-and-editor-2026-08-22.md
// section 5.3, which records why this is a kit primitive rather than
// headless-tree, react-arborist, react-complex-tree or MUI X Tree View: the
// decisive feature of the best of them is a React Native one and section 2.1
// makes that worth nothing here, the app already carries two divergent
// hand-rolled trees that a third external shape would not reduce, and the hard
// part of this tree is the shell interaction in navigation-shell §8.3 that a
// generic component argues with. Do not re-litigate the pick here.
//
// IT TAKES AN ADAPTER, NOT A URL. Everything it knows about the data is in
// `FileTreeAdapter`: a root node, and a function returning one node's children.
// So an app's source directory and the Memory scope tree are this same
// component with different adapters, and nothing about HTTP, paths or a
// response shape reaches this file. The pure half (the row model, the level
// store, the keymap) lives in `fileTree.ts` and is tested there.
//
// THE ROOT IS NOT A ROW. Its children are the top level, which is what makes
// the four required list states line up with the root level's own state:
// loading is skeleton rows, an empty root is an empty `StateBlock` with a hint,
// a failed root is an error `StateBlock` naming the root and offering retry,
// and overflow is a per-level cap with a line reporting the remainder.
//
// A FAILURE BELOW THE ROOT IS A LINE, NOT A REGION. One level failing must not
// disturb the levels already open, so it is a `Notice` sitting at that branch's
// indent with its own Retry, and every other branch stays exactly as it was.
// The test from frontend/CLAUDE.md: would this space be empty if the failure
// had not happened? Under an open branch beside four other open branches, no.
//
// Chevrons and file icons are CONTROLS, not content (design rule 3): neutral at
// rest, `--fg` on hover, never a categorical hue. All styling is in
// components.css under `.ws-tree`; there are no colors in this file.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Database,
  File as FileIcon,
  FileCode,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  Layers,
  MoreHorizontal,
  EyeOff,
} from "lucide-react";
import { cx } from "./cx";
import { Button } from "./controls";
import { Notice, Skeleton, StateBlock } from "./feedback";
import {
  flattenTree,
  levelFailed,
  levelLoading,
  levelReady,
  nodesById,
  toggleBranch,
  treeKeyIntent,
  type FileTreeAdapter,
  type FileTreeIconHint,
  type FileTreeLevels,
  type FileTreeNode,
  type FileTreeRow,
} from "./fileTree";

export type FileTreeProps = {
  /** The only thing the tree knows about the data. See `FileTreeAdapter`.
   *  Keep it stable (`useMemo`): a new adapter object means a new tree, and
   *  the tree drops every loaded level and reloads the root. */
  adapter: FileTreeAdapter;
  /** The selected node's id, if the caller tracks one. Controlled: the tree
   *  reports selection and never picks one on its own. */
  selectedId?: string | null;
  /** A row was chosen, by click or by Enter. Fires for a leaf AND a branch,
   *  because a branch is a destination in some trees (a Memory scope has its
   *  own page); a branch additionally opens or closes. */
  onSelect?: (node: FileTreeNode) => void;
  /** Branches to open on mount. Their levels load as soon as they are on
   *  screen, so this is one request per id, not a recursive walk. */
  defaultOpenIds?: string[];
  /** Rows drawn per level before the rest becomes one overflow line.
   *  Defaults to `FILE_TREE_MAX_ROWS_PER_LEVEL`. */
  maxRowsPerLevel?: number;
  /** The empty state. The hint is instructional by contract (rule 9), so it
   *  says how to put something here, not that there is nothing here. */
  emptyTitle?: string;
  emptyHint?: ReactNode;
  /** Names the tree for assistive tech: "App files", "Memory". */
  label?: string;
  className?: string;
};

const ICONS: Record<FileTreeIconHint, typeof FileIcon> = {
  folder: Folder,
  file: FileIcon,
  doc: FileText,
  code: FileCode,
  image: FileImage,
  data: Database,
  scope: Layers,
};

/** The icon for a node, from its hint and its state. Exported for `FileDrill`,
 *  the phone shape of this same tree: one tree must not have two icon tables.
 *  Not part of the package's public surface (see `index.ts`). */
export function fileTreeIconFor(node: FileTreeNode, open: boolean) {
  if (node.icon && node.icon in ICONS) {
    const Hinted = ICONS[node.icon];
    return node.icon === "folder" && open ? FolderOpen : Hinted;
  }
  if (node.kind === "branch") return open ? FolderOpen : Folder;
  return FileIcon;
}

/** Indent by depth. A CSS custom property rather than a padding value, so the
 *  step is a token and a row can still be styled from outside. */
function indent(depth: number) {
  return { "--ws-tree-depth": depth } as React.CSSProperties;
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function FileTree({
  adapter,
  selectedId,
  onSelect,
  defaultOpenIds,
  maxRowsPerLevel,
  emptyTitle = "Nothing here",
  emptyHint = "Add something at this location and it shows up here.",
  label = "Files",
  className,
}: FileTreeProps) {
  const [levels, setLevels] = useState<FileTreeLevels>({});
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set(defaultOpenIds ?? []));
  const [focusId, setFocusId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  // Bumped whenever the adapter changes, so an in-flight level from the old
  // adapter cannot land in the new tree.
  const genRef = useRef(0);

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
    load(adapter.root);
  }, [adapter, load]);

  const rows = useMemo(
    () => flattenTree(adapter.root, levels, open, { maxRowsPerLevel }),
    [adapter.root, levels, open, maxRowsPerLevel],
  );

  // A branch that is open with no level yet asks for one. This is also how
  // `defaultOpenIds` loads: the row appears, the level is missing, it is
  // fetched. One request per branch, one level per request.
  useEffect(() => {
    for (const row of rows) {
      if (row.kind === "node" && row.open && !levels[row.node.id]) load(row.node);
    }
  }, [rows, levels, load]);

  const byId = useMemo(() => nodesById(adapter.root, levels), [adapter.root, levels]);

  const retry = useCallback(
    (id: string) => {
      const node = byId.get(id);
      if (node) load(node);
    },
    [byId, load],
  );

  const activate = useCallback(
    (node: FileTreeNode) => {
      setFocusId(node.id);
      if (node.kind === "branch") setOpen((prev) => toggleBranch(prev, node.id));
      onSelect?.(node);
    },
    [onSelect],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const intent = treeKeyIntent(rows, focusId, e.key);
    if (!intent) return;
    e.preventDefault();
    if (intent.type === "focus") setFocusId(intent.id);
    else if (intent.type === "open") {
      setFocusId(intent.id);
      setOpen((prev) => toggleBranch(prev, intent.id, true));
    } else if (intent.type === "close") {
      setFocusId(intent.id);
      setOpen((prev) => toggleBranch(prev, intent.id, false));
    } else {
      setFocusId(intent.id);
      const node = byId.get(intent.id);
      if (node) onSelect?.(node);
    }
  };

  // Roving tabindex: move the DOM focus with the focused row, but only while
  // the focus is already inside this tree, so a state change elsewhere on the
  // page never steals it.
  useEffect(() => {
    if (!focusId) return;
    const el = rowRefs.current.get(focusId);
    if (el && containerRef.current?.contains(document.activeElement)) el.focus();
  }, [focusId, rows]);

  const rootState = levels[adapter.root.id];
  // Exactly one row is in the tab order (roving tabindex). The focused row
  // when it is still on screen, otherwise the first one, so collapsing the
  // branch you were standing in cannot make the tree unreachable by Tab.
  const focusOnScreen = rows.some((r) => r.kind === "node" && r.node.id === focusId);
  const tabStop = focusOnScreen ? focusId : rows.find((r) => r.kind === "node")?.key ?? null;

  if (!rootState || rootState.status === "loading") {
    return (
      <div className={cx("ws-tree", className)} aria-busy="true" aria-label={label}>
        <Skeleton width="70%" />
        <Skeleton width="55%" />
        <Skeleton width="62%" />
        <Skeleton width="40%" />
      </div>
    );
  }

  if (rootState.status === "error") {
    return (
      <div className={cx("ws-tree", className)}>
        <StateBlock
          variant="error"
          title={`Couldn't load ${adapter.root.label}`}
          hint={rootState.message}
          action={
            <Button size="sm" onClick={() => retry(adapter.root.id)}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className={cx("ws-tree", className)}>
        <StateBlock title={emptyTitle} hint={emptyHint} />
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cx("ws-tree", className)}
      role="tree"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {rows.map((row) => (
        <TreeRow
          key={row.key}
          row={row}
          selectedId={selectedId ?? null}
          isTabStop={row.key === tabStop}
          onActivate={activate}
          onFocusRow={setFocusId}
          onToggle={(node) => setOpen((prev) => toggleBranch(prev, node.id))}
          onRetry={retry}
          register={(el) => {
            if (el) rowRefs.current.set(row.key, el);
            else rowRefs.current.delete(row.key);
          }}
        />
      ))}
    </div>
  );
}

function TreeRow({
  row,
  selectedId,
  isTabStop,
  onActivate,
  onFocusRow,
  onToggle,
  onRetry,
  register,
}: {
  row: FileTreeRow;
  selectedId: string | null;
  isTabStop: boolean;
  onActivate: (node: FileTreeNode) => void;
  /** Tabbing into the tree lands on a row without any keystroke, so the row
   *  itself has to say where the arrow keys start from. */
  onFocusRow: (id: string) => void;
  onToggle: (node: FileTreeNode) => void;
  onRetry: (parentId: string) => void;
  register: (el: HTMLDivElement | null) => void;
}) {
  if (row.kind === "loading") {
    return (
      <div className="ws-tree__aside" style={indent(row.depth)} aria-busy="true">
        <Skeleton width="55%" />
      </div>
    );
  }

  if (row.kind === "error") {
    // A LINE, not a region: the branches around this one are still on screen
    // and keeping them there is the point.
    return (
      <div className="ws-tree__aside" style={indent(row.depth)}>
        <Notice
          tone="error"
          detail={row.message}
          action={
            <Button size="sm" variant="ghost" onClick={() => onRetry(row.parentId)}>
              Retry
            </Button>
          }
        >
          Couldn't load {row.label}.
        </Notice>
      </div>
    );
  }

  if (row.kind === "empty") {
    return (
      <div className="ws-tree__aside ws-tree__aside--muted" style={indent(row.depth)}>
        Empty
      </div>
    );
  }

  if (row.kind === "overflow") {
    return (
      <div className="ws-tree__aside ws-tree__aside--muted" style={indent(row.depth)}>
        <MoreHorizontal aria-hidden />
        {row.hidden} more not shown. Open a narrower folder to see them.
      </div>
    );
  }

  if (row.kind === "filtered") {
    return (
      <div className="ws-tree__aside ws-tree__aside--muted" style={indent(row.depth)}>
        <EyeOff aria-hidden />
        Hidden: {row.names.join(", ")}
      </div>
    );
  }

  const { node, open } = row;
  const Icon = fileTreeIconFor(node, open);
  const selected = node.id === selectedId;
  const Twist = open ? ChevronDown : ChevronRight;

  return (
    <div
      ref={register}
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={node.kind === "branch" ? open : undefined}
      tabIndex={isTabStop ? 0 : -1}
      className={cx("ws-tree__row", selected && "is-selected")}
      style={indent(row.depth)}
      onClick={() => onActivate(node)}
      onFocus={() => onFocusRow(node.id)}
    >
      {node.kind === "branch" ? (
        <span
          className="ws-tree__twist"
          aria-hidden
          onClick={(e) => {
            e.stopPropagation();
            onToggle(node);
          }}
        >
          <Twist />
        </span>
      ) : (
        <span className="ws-tree__twist ws-tree__twist--empty" aria-hidden />
      )}
      <Icon className="ws-tree__icon" aria-hidden />
      <span className="ws-tree__label">{node.label}</span>
      {node.detail != null && <span className="ws-tree__detail">{node.detail}</span>}
    </div>
  );
}
