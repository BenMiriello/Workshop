// The pure rules behind Memory's tree: what a node IS, how a node id is
// written down, what sits inside a branch, and where "up" goes.
//
// No React, no fetch, no DOM, so all of it runs in vitest (memoryTree.test.ts).
// The repo has no render harness and none is planned, so this file is
// deliberately where the decidable half of the Memory tree lives, exactly as
// `packages/ui/src/fileTree.ts` is for the tree primitive itself.
//
// A NOTE IS A SCOPE AND AN ID, NOT A PATH. That is the rule from
// docs/platform-redesign/file-viewer-and-editor-2026-08-22.md section 6:
// "Memory keeps its own addressing. A note is a scope and an id, not a
// directory path, and this design does not turn it into one. It gets the same
// viewer, the same editor and the same tree primitive through a different
// adapter." So the node id below is a tagged union written as a string, not a
// filesystem path with notes pretending to be files in it.
//
// The docs half of the tree IS paths, because docs/ is a real committed
// directory. Both live under one root, which is what makes them one tree.
import type { FileTreeNode } from "@workshop/ui";
import type { components } from "../../api/schema.gen";
import { scopeLabel } from "../../lib/tasks";

export type ScopeNode = components["schemas"]["ScopeNode"];
export type NoteRow = components["schemas"]["NoteRow"];
export type DocRow = components["schemas"]["DocRow"];

/** Every kind of thing the Memory tree can point at. */
export type MemoryNode =
  | { kind: "root" }
  /** A knowledge scope (`workshop`, `project:birds`, `agent:x`). */
  | { kind: "scope"; scope: string }
  /** One note, addressed the way the knowledge store addresses it. */
  | { kind: "note"; scope: string; id: string }
  /** The committed docs/ tree. */
  | { kind: "docs" }
  /** A directory inside docs/, by repo-relative path. */
  | { kind: "docdir"; path: string }
  /** One markdown file, by repo-relative path. */
  | { kind: "doc"; path: string };

/** The repo-relative root of the committed documents tree. */
export const DOCS_ROOT = "docs";

export const ROOT_ID = "root";
export const DOCS_ID = "docs";

/**
 * A node as a string id, unique across the WHOLE tree (the tree primitive uses
 * it as the row key, the level key and the selection key).
 *
 * `#` separates a note's scope from its id because a scope key contains colons
 * (`project:birds`) and a note id is a slug that cannot contain either.
 */
export function encodeMemoryNode(node: MemoryNode): string {
  switch (node.kind) {
    case "root": return ROOT_ID;
    case "docs": return DOCS_ID;
    case "scope": return `scope:${node.scope}`;
    case "note": return `note:${node.scope}#${node.id}`;
    case "docdir": return `dir:${node.path}`;
    case "doc": return `doc:${node.path}`;
  }
}

/** The inverse. Returns null for anything this tree did not write. */
export function parseMemoryNode(id: string): MemoryNode | null {
  if (id === ROOT_ID) return { kind: "root" };
  if (id === DOCS_ID) return { kind: "docs" };
  const at = id.indexOf(":");
  if (at < 0) return null;
  const kind = id.slice(0, at);
  const rest = id.slice(at + 1);
  if (!rest) return null;
  if (kind === "scope") return { kind: "scope", scope: rest };
  if (kind === "dir") return { kind: "docdir", path: rest };
  if (kind === "doc") return { kind: "doc", path: rest };
  if (kind === "note") {
    const hash = rest.lastIndexOf("#");
    if (hash <= 0 || hash === rest.length - 1) return null;
    return { kind: "note", scope: rest.slice(0, hash), id: rest.slice(hash + 1) };
  }
  return null;
}

/** The scopes directly inside `parent` (null for the top level), in the order
 *  the backend already sorted them into. */
export function childScopes(tree: readonly ScopeNode[], parent: string | null): string[] {
  return tree.filter((n) => (n.parent ?? null) === parent).map((n) => n.key);
}

/** The notes filed to EXACTLY this scope. `GET /api/notes?scope=` answers with
 *  the scope and everything below it (`scope_tree.reach`), which is right for a
 *  filter and wrong for a level: a note filed to a child scope belongs on the
 *  child's row, not repeated on its parent's. */
export function notesInScope(notes: readonly NoteRow[], scope: string): NoteRow[] {
  return notes.filter((n) => n.scope === scope);
}

/** The last segment of a path. */
export function baseName(path: string): string {
  const at = path.lastIndexOf("/");
  return at < 0 ? path : path.slice(at + 1);
}

/** The containing directory of a path, or "" at the top. */
export function dirName(path: string): string {
  const at = path.lastIndexOf("/");
  return at < 0 ? "" : path.slice(0, at);
}

/** One level of the docs tree: the directories directly inside `dir`, then the
 *  markdown files directly inside it. `GET /api/documents` already walks the
 *  whole tree in one response, so a level is a grouping rather than a request. */
export function docLevel(
  docs: readonly DocRow[],
  dir: string,
): { dirs: string[]; files: DocRow[] } {
  const prefix = dir.endsWith("/") ? dir : `${dir}/`;
  const dirs = new Set<string>();
  const files: DocRow[] = [];
  for (const doc of docs) {
    if (!doc.path.startsWith(prefix)) continue;
    const rest = doc.path.slice(prefix.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash < 0) files.push(doc);
    else dirs.add(prefix + rest.slice(0, slash));
  }
  files.sort((a, b) => {
    const at = (a.title || baseName(a.path)).toLowerCase();
    const bt = (b.title || baseName(b.path)).toLowerCase();
    return at < bt ? -1 : at > bt ? 1 : a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
  });
  return { dirs: Array.from(dirs).sort(), files };
}

/** Where `Up` goes from here, which is structural containment and never
 *  history (navigation-shell-2026-08-20.md section 4.4: "Up is structural and
 *  belongs to the page. Back is history and belongs to the browser."). */
export function parentOf(node: MemoryNode, tree: readonly ScopeNode[]): MemoryNode | null {
  switch (node.kind) {
    case "root":
      return null;
    case "docs":
      return { kind: "root" };
    case "scope": {
      const found = tree.find((n) => n.key === node.scope);
      const parent = found?.parent ?? null;
      return parent ? { kind: "scope", scope: parent } : { kind: "root" };
    }
    case "note":
      return { kind: "scope", scope: node.scope };
    case "docdir":
    case "doc": {
      const dir = dirName(node.path);
      if (!dir || dir === DOCS_ROOT) return { kind: "docs" };
      return { kind: "docdir", path: dir };
    }
  }
}

/** Every ancestor of `node`, root first, `node` itself excluded. This is what
 *  the tree opens on mount so a deep link lands with its branch already open,
 *  and it is the trail the phone shows above the level it is listing. */
export function ancestorsOf(node: MemoryNode, tree: readonly ScopeNode[]): MemoryNode[] {
  const out: MemoryNode[] = [];
  let cur = parentOf(node, tree);
  // A malformed scope tree (a cycle in `parent`) would otherwise spin here.
  const seen = new Set<string>();
  while (cur) {
    const id = encodeMemoryNode(cur);
    if (seen.has(id)) break;
    seen.add(id);
    out.push(cur);
    cur = parentOf(cur, tree);
  }
  return out.reverse();
}

/** Whether a node is a container (a branch in the tree) or a destination. */
export function isBranch(node: MemoryNode): boolean {
  return node.kind === "root" || node.kind === "scope" || node.kind === "docs" || node.kind === "docdir";
}

// ---- The kit's vocabulary --------------------------------------------------
// The tree on screen is the kit primitive (`FileTree` on a desktop, `FileDrill`
// on a phone), which speaks `FileTreeNode`. These two rules are the whole
// translation, and they are here rather than beside the fetching because they
// are decidable and therefore testable.

/** What a node reads as. Every branch a trail can contain is nameable without
 *  another request, which is what lets the phone draw its trail synchronously. */
export function nodeLabel(node: MemoryNode): string {
  switch (node.kind) {
    case "root": return "Memory";
    case "docs": return "Documents";
    case "scope": return scopeLabel(node.scope);
    case "docdir": return baseName(node.path);
    case "doc": return baseName(node.path);
    case "note": return node.id;
  }
}

/** A `MemoryNode` as the row the kit tree draws. A note and a doc carry a
 *  stored title, which only the fetched row knows, so this labels a leaf from
 *  its address and the level builder overrides it; every BRANCH is named
 *  correctly from the node alone, which is what a trail needs. */
export function memoryFileNode(node: MemoryNode): FileTreeNode {
  const icon =
    node.kind === "root" || node.kind === "scope"
      ? "scope"
      : node.kind === "docs" || node.kind === "docdir"
        ? "folder"
        : "doc";
  return {
    id: encodeMemoryNode(node),
    label: nodeLabel(node),
    kind: isBranch(node) ? "branch" : "leaf",
    icon,
  };
}

/**
 * The branches between the root and `node`'s own level, outermost first: what
 * the kit's `drillTrail` takes as its `ancestors`. The root is excluded because
 * a trail always starts there.
 *
 * WHY MEMORY RECORDS AN EXPLICIT TRAIL LIKE EVERY OTHER CALLER, even though it
 * is the one adapter that could answer "what contains this". `FileDrill` takes
 * its trail from the caller because a `FileTreeNode` knows its own id and
 * nothing about what encloses it, so the alternative here was an optional
 * `parent` on `FileTreeAdapter` and a second way for the drill to find its way
 * out. That was rejected on three counts. A second path through the kit is
 * exactly the duplication this fold removes, and it would be a path only one
 * caller ever exercised. `parentOf` is total only once the scope tree has
 * loaded, so the kit would grow a fourth, asynchronous state purely to ask a
 * question the caller can already answer synchronously. And a recorded trail is
 * strictly more general: every caller can build one, only Memory could supply a
 * parent. None of Memory's ancestry is wasted for it, because that ancestry is
 * what this function turns into `drillTrail`'s `ancestors`, the same seam the
 * Files tab fills from a path.
 */
export function drillAncestorsFor(
  node: MemoryNode,
  tree: readonly ScopeNode[],
): FileTreeNode[] {
  return ancestorsOf(node, tree)
    .filter((n) => n.kind !== "root")
    .map(memoryFileNode);
}

/**
 * WHERE A NEW NOTE LANDS: the scope you are standing in.
 *
 * The old New note control opened a modal whose first field was a scope
 * dropdown defaulting to the first scope in the list, so the answer to "where
 * does this go" was both unasked and unshown until you noticed the dropdown.
 * The operator's read on 2026-08-27: "there's no way to pick where it goes,
 * there's no indication of where it will go", and then the rule he wanted --
 * "if we're doing a file picker folder tree viewer, then whatever you're in
 * should dictate where it's gonna be. You go to the place where you want to
 * create it, and then you create it there."
 *
 * So the tree IS the picker. A note or a scope tells you its scope directly. A
 * DOCUMENT does not: `docs/` is a committed directory, not a scope, and a note
 * cannot be filed into it, so standing on one falls back rather than inventing
 * a scope out of a path. `fallback` is the caller's answer for that, and for
 * the root.
 */
export function scopeForNewNote(
  selected: MemoryNode | null,
  fallback: string,
): string {
  if (!selected) return fallback;
  switch (selected.kind) {
    case "note":
    case "scope":
      return selected.scope;
    default:
      return fallback;
  }
}

// ---- URL addressing --------------------------------------------------------
// Memory's existing deep links are `?scope=&note=` (services/refs.py's note
// resolver) and `?path=` (its doc resolver). The tree SELECTS through those
// same two, so every link already handed out keeps working and the selection
// stays shareable.

export type MemorySelection = {
  scope?: string | null;
  note?: string | null;
  path?: string | null;
};

/** The query parameters that select `node`, or null for a node that is a
 *  container only. A scope selects itself so the phone's trail can be linked. */
export function paramsForNode(node: MemoryNode): Record<string, string> | null {
  switch (node.kind) {
    case "note": return { scope: node.scope, note: node.id };
    case "doc": return { path: node.path };
    case "scope": return { scope: node.scope };
    default: return null;
  }
}

/** What the current query parameters are pointing at, or null. `path` wins
 *  over `note`, because a doc link carries only `path` while a note link
 *  carries both `scope` and `note`, so there is no ambiguity to resolve. */
export function nodeForParams(sel: MemorySelection): MemoryNode | null {
  const path = (sel.path || "").trim();
  if (path) return { kind: "doc", path };
  const note = (sel.note || "").trim();
  const scope = (sel.scope || "").trim();
  if (note && scope) return { kind: "note", scope, id: note };
  if (scope && scope !== "all") return { kind: "scope", scope };
  return null;
}

// ---- Search ----------------------------------------------------------------
// The tree is containment; a search is a query, and per
// navigation-shell-2026-08-20.md section 3.5 a query result is a list and never
// a tree. Notes are filtered server-side by `GET /api/notes?q=`; docs have no
// search route, so their titles and paths are matched here.

export function searchDocs(docs: readonly DocRow[], query: string): DocRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return docs.filter(
    (d) => d.title.toLowerCase().includes(q) || d.path.toLowerCase().includes(q),
  );
}

/**
 * A markdown document's title: its first `# ` heading, else `fallback`.
 *
 * The same rule `routes/docs_index.py:_title_of` applies when it builds the
 * index, restated here because the file read (`GET /api/files/content`) answers
 * with bytes and a path and does not carry the index's title. Reading it off the
 * content is what keeps the heading on screen in step with an edit that just
 * changed it.
 */
export function titleOfMarkdown(content: string, fallback: string): string {
  for (const line of content.split("\n", 50)) {
    const m = /^\s*#\s+(.+?)\s*$/.exec(line);
    if (m) return m[1];
  }
  return fallback;
}
