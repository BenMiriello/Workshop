// The pure rules behind `FileTree`: the node/adapter vocabulary, the level
// store, flattening an open tree into a flat list of rows, and what a keystroke
// means. No React and no DOM, so all of it is executable in vitest
// (`fileTree.test.ts`). The repo has no render harness and none is planned, so
// this file is deliberately where the decidable part of the tree lives.
// `frontend/src/pages/apps/tabs/filePath.ts` is the pattern.
//
// THE ADAPTER IS THE POINT. Nothing here knows about URLs, HTTP, paths or the
// file API's response shape. The tree renders *nodes with lazy children*, so a
// filesystem directory and the Memory scope tree are the same component with
// different adapters. See
// docs/platform-redesign/file-viewer-and-editor-2026-08-22.md section 5.3,
// which records why this is a kit primitive and not one of the four tree
// libraries that were evaluated.
//
// THE ROW LIST IS FLAT ON PURPOSE. One array, one stable key per row, one
// depth per row, no nesting. Virtualization is parked (section 8 item 4) until
// a real directory is measurably slow, at which point `@tanstack/react-virtual`
// takes exactly this shape and no row model has to be rewritten.

/** A branch can be opened to reveal children; a leaf is a destination. */
export type FileTreeNodeKind = "branch" | "leaf";

/** What the node IS, in the adapter's words, so the tree can pick an icon
 *  without knowing the domain. It is a hint: an unrecognised value and an
 *  absent one both fall back to the icon for the node's `kind`. */
export type FileTreeIconHint =
  | "folder"
  | "file"
  | "doc"
  | "code"
  | "image"
  | "data"
  | "scope";

export type FileTreeNode = {
  /** Unique across the WHOLE tree, not just its level: it is the row key, the
   *  level key and what selection and open-state are recorded against. A
   *  filesystem adapter uses the path; the Memory adapter uses scope + id. A
   *  repeated id is dropped rather than rendered twice, which also makes a
   *  cyclic adapter terminate instead of hanging the page. */
  id: string;
  label: string;
  kind: FileTreeNodeKind;
  icon?: FileTreeIconHint;
  /** Trailing muted text on the row: a size, a count, a modified time. The
   *  adapter formats it, because only the adapter knows what it is. */
  detail?: string;
};

/** One level, as the adapter returns it. `filtered` reports what the source
 *  excluded from this listing (the file API drops `.git/`, `node_modules/`,
 *  `.venv/`, `__pycache__/`), so filtering is visible on the row instead of
 *  silent. */
export type FileTreeLevel = {
  nodes: FileTreeNode[];
  filtered?: string[];
};

/** The whole seam. A root, and a function returning one node's children.
 *  One level per request, never a recursive walk. */
export type FileTreeAdapter = {
  /** The root's own row is never rendered: its children are the top level, and
   *  its `label` is what the error state names when the top level fails. */
  root: FileTreeNode;
  children: (node: FileTreeNode) => Promise<FileTreeLevel>;
};

export type FileTreeLevelState =
  | { status: "loading" }
  | { status: "ready"; nodes: FileTreeNode[]; filtered?: string[] }
  | { status: "error"; message: string };

/** Keyed by the PARENT node's id. Levels are independent by construction,
 *  which is the whole reason one failing level cannot take down another. */
export type FileTreeLevels = Readonly<Record<string, FileTreeLevelState>>;

export type FileTreeRow =
  | { kind: "node"; key: string; depth: number; node: FileTreeNode; open: boolean }
  | { kind: "loading"; key: string; depth: number; parentId: string; label: string }
  | { kind: "error"; key: string; depth: number; parentId: string; label: string; message: string }
  | { kind: "empty"; key: string; depth: number; parentId: string; label: string }
  | { kind: "overflow"; key: string; depth: number; parentId: string; hidden: number }
  | { kind: "filtered"; key: string; depth: number; parentId: string; names: string[] };

/** Rows rendered per level before the rest becomes an overflow line. Not
 *  virtualization and not a substitute for it: it is the fourth list state,
 *  so a directory of 20,000 files reports itself instead of freezing the tab. */
export const FILE_TREE_MAX_ROWS_PER_LEVEL = 500;

// ---- The level store -------------------------------------------------------
// Three transitions, each returning a NEW map that shares every untouched
// entry. That sharing is the failure rule: `levelFailed` can only ever replace
// the key it is given.

export function levelLoading(levels: FileTreeLevels, id: string): FileTreeLevels {
  return { ...levels, [id]: { status: "loading" } };
}

export function levelReady(
  levels: FileTreeLevels,
  id: string,
  level: FileTreeLevel,
): FileTreeLevels {
  const next: FileTreeLevelState = { status: "ready", nodes: level.nodes };
  if (level.filtered && level.filtered.length > 0) next.filtered = level.filtered;
  return { ...levels, [id]: next };
}

/** A level that could not load. Every other level is preserved verbatim: the
 *  branches already open keep their contents on screen, and the failure is
 *  reported as a LINE beside them rather than a region that replaces them
 *  (frontend/CLAUDE.md, "A region or a line"). */
export function levelFailed(
  levels: FileTreeLevels,
  id: string,
  message: string,
): FileTreeLevels {
  return { ...levels, [id]: { status: "error", message } };
}

/** Retry: drop the failed level so the tree asks for it again. */
export function levelForget(levels: FileTreeLevels, id: string): FileTreeLevels {
  if (!(id in levels)) return levels;
  const next = { ...levels };
  delete next[id];
  return next;
}

// ---- Open state ------------------------------------------------------------

/** Open or close one branch. `want` forces a direction (opening a branch that
 *  is already open must not close it), otherwise it toggles. */
export function toggleBranch(
  open: ReadonlySet<string>,
  id: string,
  want?: boolean,
): Set<string> {
  const next = new Set(open);
  const shouldOpen = want ?? !next.has(id);
  if (shouldOpen) next.add(id);
  else next.delete(id);
  return next;
}

// ---- Flattening ------------------------------------------------------------

export type FlattenOptions = { maxRowsPerLevel?: number };

/** The open tree as a flat row list, each row carrying its depth.
 *
 *  The ROOT is not a row, and its four states are the caller's four list
 *  states: while the root level is loading or missing this returns no rows (the
 *  caller renders skeletons), an errored root level returns no rows (an error
 *  `StateBlock`, because nothing is on screen), and an empty root level returns
 *  no rows (an empty `StateBlock`). Below the root the same three situations
 *  are lines beside content that IS on screen, so they come back as `loading`,
 *  `error` and `empty` rows at the right depth. */
export function flattenTree(
  root: FileTreeNode,
  levels: FileTreeLevels,
  open: ReadonlySet<string>,
  opts: FlattenOptions = {},
): FileTreeRow[] {
  const max = opts.maxRowsPerLevel ?? FILE_TREE_MAX_ROWS_PER_LEVEL;
  const rows: FileTreeRow[] = [];
  const seen = new Set<string>([root.id]);

  const emit = (parent: FileTreeNode, depth: number, isRoot: boolean) => {
    const state = levels[parent.id];
    if (!state || state.status === "loading") {
      if (!isRoot) {
        rows.push({ kind: "loading", key: `${parent.id}::loading`, depth, parentId: parent.id, label: parent.label });
      }
      return;
    }
    if (state.status === "error") {
      if (!isRoot) {
        rows.push({
          kind: "error",
          key: `${parent.id}::error`,
          depth,
          parentId: parent.id,
          label: parent.label,
          message: state.message,
        });
      }
      return;
    }
    if (state.nodes.length === 0) {
      if (!isRoot) {
        rows.push({ kind: "empty", key: `${parent.id}::empty`, depth, parentId: parent.id, label: parent.label });
      }
    }
    const shown = max > 0 ? state.nodes.slice(0, max) : state.nodes;
    for (const node of shown) {
      if (seen.has(node.id)) continue;
      seen.add(node.id);
      const isOpen = node.kind === "branch" && open.has(node.id);
      rows.push({ kind: "node", key: node.id, depth, node, open: isOpen });
      if (isOpen) emit(node, depth + 1, false);
    }
    if (state.nodes.length > shown.length) {
      rows.push({
        kind: "overflow",
        key: `${parent.id}::overflow`,
        depth,
        parentId: parent.id,
        hidden: state.nodes.length - shown.length,
      });
    }
    if (state.filtered && state.filtered.length > 0) {
      rows.push({
        kind: "filtered",
        key: `${parent.id}::filtered`,
        depth,
        parentId: parent.id,
        names: state.filtered,
      });
    }
  };

  emit(root, 0, true);
  return rows;
}

/** Every node the tree currently knows about, by id. The row model carries
 *  parent ids rather than parent nodes, so this is how a retry line gets back
 *  the node it has to re-request. */
export function nodesById(
  root: FileTreeNode,
  levels: FileTreeLevels,
): Map<string, FileTreeNode> {
  const byId = new Map<string, FileTreeNode>([[root.id, root]]);
  for (const state of Object.values(levels)) {
    if (state.status !== "ready") continue;
    for (const node of state.nodes) if (!byId.has(node.id)) byId.set(node.id, node);
  }
  return byId;
}

// ---- Keyboard --------------------------------------------------------------

export type FileTreeIntent =
  | { type: "focus"; id: string }
  | { type: "open"; id: string }
  | { type: "close"; id: string }
  | { type: "select"; id: string };

/** What a keystroke means over the CURRENT row list, or `null` for a key this
 *  tree does not claim (the caller must not `preventDefault` on those).
 *
 *  Up and down move, right and left open and close a branch, Enter selects.
 *  The two arrows do double duty the way every tree does: right on a branch
 *  that is already open steps into its first child, and left on a leaf or a
 *  closed branch steps out to its parent. */
export function treeKeyIntent(
  rows: readonly FileTreeRow[],
  focusedId: string | null,
  key: string,
): FileTreeIntent | null {
  const items = rows.filter((r): r is Extract<FileTreeRow, { kind: "node" }> => r.kind === "node");
  if (items.length === 0) return null;
  const at = focusedId == null ? -1 : items.findIndex((r) => r.node.id === focusedId);

  const focus = (i: number): FileTreeIntent | null =>
    i >= 0 && i < items.length ? { type: "focus", id: items[i].node.id } : null;

  if (at < 0) {
    // Nothing focused yet: the first key that moves lands somewhere sensible.
    if (key === "ArrowDown" || key === "Home") return focus(0);
    if (key === "ArrowUp" || key === "End") return focus(items.length - 1);
    return null;
  }

  const row = items[at];
  switch (key) {
    case "ArrowDown":
      return focus(at + 1);
    case "ArrowUp":
      return focus(at - 1);
    case "Home":
      return focus(0);
    case "End":
      return focus(items.length - 1);
    case "ArrowRight":
      if (row.node.kind !== "branch") return null;
      if (!row.open) return { type: "open", id: row.node.id };
      return items[at + 1] && items[at + 1].depth > row.depth ? focus(at + 1) : null;
    case "ArrowLeft": {
      if (row.node.kind === "branch" && row.open) return { type: "close", id: row.node.id };
      for (let i = at - 1; i >= 0; i--) if (items[i].depth < row.depth) return focus(i);
      return null;
    }
    case "Enter":
      return { type: "select", id: row.node.id };
    default:
      return null;
  }
}
