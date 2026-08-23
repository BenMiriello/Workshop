// Memory's tree: the adapter, the desktop tree, and the phone's drill-in list.
//
// THE ADAPTER IS THE WHOLE POINT. `FileTree` in the kit renders nodes with lazy
// children and knows nothing about HTTP, paths or scopes
// (file-viewer-and-editor-2026-08-22.md section 5.3: "a directory and the memory
// scope tree are the same component with different adapters"). This file is that
// second adapter. It reads three endpoints that already exist and adds none:
//
//   GET /api/notes/scopes/tree   the scope tree, root-first, with parents
//   GET /api/notes?scope=<key>   the notes a scope reaches, narrowed to exact
//   GET /api/documents           the committed docs/ index
//
// A NOTE IS A SCOPE AND AN ID. Nothing here turns one into a file path; see
// memoryTree.ts, where the node vocabulary and every decidable rule live and are
// tested. This file is the fetching and the rendering, and nothing else.
//
// TWO SHAPES, ONE SOURCE. On a desktop the tree is the kit primitive, drawn in
// column two by the shell takeover. On a phone it is this file's drill-in list,
// which is the page's own CONTENT and walks one level at a time with `Up`
// (navigation-shell-2026-08-20.md section 4.4: "Up is structural and belongs to
// the page. Back is history and belongs to the browser."). It does not touch the
// tab bar. Both read the same `MemorySource`, so a level means the same thing on
// both.
import { useCallback, useEffect, useState } from "react";
import { ChevronUp, FileText, Folder, Layers, MoreHorizontal } from "lucide-react";
import {
  Button,
  FileTree,
  Skeleton,
  StateBlock,
  type FileTreeAdapter,
  type FileTreeLevel,
  type FileTreeNode,
} from "@workshop/ui";
import { scopeLabel } from "../../lib/tasks";
import { getJson } from "./api";
import {
  DOCS_ROOT,
  ancestorsOf,
  baseName,
  childScopes,
  docLevel,
  encodeMemoryNode,
  notesInScope,
  parentOf,
  parseMemoryNode,
  type DocRow,
  type MemoryNode,
  type NoteRow,
  type ScopeNode,
} from "./memoryTree";

/** Rows a phone level draws before the rest becomes one line. The fourth list
 *  state: a scope with 4,000 notes reports itself instead of freezing the tab. */
const DRILL_MAX_ROWS = 100;

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

function scopeNode(key: string): FileTreeNode {
  return { id: encodeMemoryNode({ kind: "scope", scope: key }), label: scopeLabel(key), kind: "branch", icon: "scope" };
}

function noteNode(note: NoteRow): FileTreeNode {
  return {
    id: encodeMemoryNode({ kind: "note", scope: note.scope, id: note.id }),
    label: note.title || note.id,
    kind: "leaf",
    icon: "doc",
  };
}

function docDirNode(path: string): FileTreeNode {
  return { id: encodeMemoryNode({ kind: "docdir", path }), label: baseName(path), kind: "branch", icon: "folder" };
}

function docNode(doc: DocRow): FileTreeNode {
  return {
    id: encodeMemoryNode({ kind: "doc", path: doc.path }),
    label: doc.title || baseName(doc.path),
    kind: "leaf",
    icon: "doc",
  };
}

const DOCS_NODE: FileTreeNode = {
  id: encodeMemoryNode({ kind: "docs" }),
  label: "Documents",
  kind: "branch",
  icon: "folder",
};

export const MEMORY_ROOT_NODE: FileTreeNode = {
  id: encodeMemoryNode({ kind: "root" }),
  label: "Memory",
  kind: "branch",
  icon: "scope",
};

export type MemorySource = {
  /** For the kit primitive. */
  adapter: FileTreeAdapter;
  /** For the phone's level list, and for anything that has a `MemoryNode`
   *  rather than a `FileTreeNode`. */
  children: (node: MemoryNode) => Promise<FileTreeLevel>;
  /** The scope tree, cached. `Up` needs it to know a scope's parent. */
  scopes: () => Promise<ScopeNode[]>;
  /** The docs index, cached. A search over documents reads it; there is no
   *  doc search route and this response already carries every title and path. */
  docs: () => Promise<DocRow[]>;
};

/**
 * One source, with the two whole-tree reads cached in it.
 *
 * The scope tree and the docs index are each ONE response describing the whole
 * structure, so caching them is what makes a level a grouping rather than a
 * request. Notes are fetched per scope, because that is the only shape the
 * notes endpoint has. A failed cache entry is dropped, so the tree's Retry
 * really does retry rather than replaying the same rejection.
 *
 * Build it in a `useMemo`: a new source means a new adapter, and the tree drops
 * every level it had loaded and reloads. That is exactly what a refresh wants
 * and exactly what an accidental re-render does not.
 */
export function createMemorySource(): MemorySource {
  let scopesP: Promise<ScopeNode[]> | null = null;
  let docsP: Promise<DocRow[]> | null = null;

  const scopes = (): Promise<ScopeNode[]> => {
    if (!scopesP) {
      scopesP = getJson<{ scopes?: ScopeNode[] }>("/api/notes/scopes/tree")
        .then((j) => j.scopes ?? [])
        .catch((e) => { scopesP = null; throw e; });
    }
    return scopesP;
  };

  const docs = (): Promise<DocRow[]> => {
    if (!docsP) {
      docsP = getJson<{ docs?: DocRow[] }>("/api/documents")
        .then((j) => j.docs ?? [])
        .catch((e) => { docsP = null; throw e; });
    }
    return docsP;
  };

  const children = async (node: MemoryNode): Promise<FileTreeLevel> => {
    switch (node.kind) {
      case "root": {
        const tree = await scopes();
        return { nodes: [...childScopes(tree, null).map(scopeNode), DOCS_NODE] };
      }
      case "scope": {
        const [tree, notes] = await Promise.all([
          scopes(),
          getJson<{ notes?: NoteRow[] }>(`/api/notes?scope=${encodeURIComponent(node.scope)}`)
            .then((j) => j.notes ?? []),
        ]);
        return {
          nodes: [
            ...childScopes(tree, node.scope).map(scopeNode),
            ...notesInScope(notes, node.scope).map(noteNode),
          ],
        };
      }
      case "docs":
      case "docdir": {
        const all = await docs();
        const dir = node.kind === "docs" ? DOCS_ROOT : node.path;
        const level = docLevel(all, dir);
        return { nodes: [...level.dirs.map(docDirNode), ...level.files.map(docNode)] };
      }
      default:
        // A leaf. The tree never asks, and answering with an empty level is
        // still the right answer if something ever does.
        return { nodes: [] };
    }
  };

  const adapter: FileTreeAdapter = {
    root: MEMORY_ROOT_NODE,
    children: (node) => {
      const parsed = parseMemoryNode(node.id);
      if (!parsed) return Promise.resolve({ nodes: [] });
      return children(parsed);
    },
  };

  return { adapter, children, scopes, docs };
}

// ---- Desktop: the kit primitive -------------------------------------------

export function MemoryTreeView({
  source,
  selected,
  onSelect,
}: {
  source: MemorySource;
  selected: MemoryNode | null;
  onSelect: (node: MemoryNode) => void;
}) {
  // The scope tree is needed before the primitive mounts, not after: which
  // branches start open is read ONCE by `FileTree` (a deep link into
  // `project:birds` should land with that scope unfolded), and a scope's parent
  // is only knowable from this response. It is the same cached request the root
  // level makes, so waiting for it here costs one render and no traffic.
  //
  // Mounting once is also what keeps section 3.4's fourth correction true:
  // nothing but the operator collapses a branch they opened.
  const [scopeTree, setScopeTree] = useState<ScopeNode[] | null>(null);
  useEffect(() => {
    let live = true;
    source.scopes().then(
      (s) => { if (live) setScopeTree(s); },
      // A failure here is not swallowed: the root level asks for the same
      // response and reports it as the tree's own error state, with Retry.
      () => { if (live) setScopeTree([]); },
    );
    return () => { live = false; };
  }, [source]);

  if (scopeTree === null) {
    return (
      <div className="ws-memory__tree" aria-busy="true" aria-label="Memory">
        <Skeleton width="70%" />
        <Skeleton width="55%" />
        <Skeleton width="62%" />
        <Skeleton width="40%" />
      </div>
    );
  }

  const defaultOpenIds = selected
    ? ancestorsOf(selected, scopeTree).filter((n) => n.kind !== "root").map(encodeMemoryNode)
    : [];

  return (
    <FileTree
      className="ws-memory__tree"
      label="Memory"
      adapter={source.adapter}
      defaultOpenIds={defaultOpenIds}
      selectedId={selected ? encodeMemoryNode(selected) : null}
      emptyTitle="Nothing in Memory yet"
      emptyHint="Write a note with New note, or an agent will leave one as it works."
      onSelect={(node) => {
        const parsed = parseMemoryNode(node.id);
        if (parsed) onSelect(parsed);
      }}
    />
  );
}

// ---- Phone: one level, with Up ---------------------------------------------

type LevelState =
  | { status: "loading" }
  | { status: "ready"; nodes: FileTreeNode[] }
  | { status: "error"; message: string };

export function MemoryDrill({
  source,
  cursor,
  onCursor,
  selected,
  onSelect,
  scopeTree,
}: {
  source: MemorySource;
  /** The branch being listed. */
  cursor: MemoryNode;
  onCursor: (node: MemoryNode) => void;
  selected: MemoryNode | null;
  onSelect: (node: MemoryNode) => void;
  scopeTree: readonly ScopeNode[];
}) {
  const [level, setLevel] = useState<LevelState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setLevel({ status: "loading" });
    source.children(cursor).then(
      (l) => { if (live) setLevel({ status: "ready", nodes: l.nodes }); },
      (e: unknown) => {
        if (live) setLevel({ status: "error", message: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => { live = false; };
  }, [source, cursor, attempt]);

  const up = parentOf(cursor, scopeTree);
  const selectedId = selected ? encodeMemoryNode(selected) : null;
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const trail = (
    <div className="ws-memory__trail">
      {up && (
        <Button size="sm" variant="ghost" onClick={() => onCursor(up)} aria-label={`Up to ${nodeLabel(up)}`}>
          <ChevronUp aria-hidden /> Up
        </Button>
      )}
      <span className="ws-memory__trailname">{nodeLabel(cursor)}</span>
    </div>
  );

  if (level.status === "loading") {
    return (
      <div aria-busy="true">
        {trail}
        <Skeleton width="70%" />
        <Skeleton width="55%" />
        <Skeleton width="62%" />
      </div>
    );
  }

  if (level.status === "error") {
    return (
      <div>
        {trail}
        <StateBlock
          variant="error"
          title={`Couldn't load ${nodeLabel(cursor)}`}
          hint={level.message}
          action={<Button size="sm" onClick={retry}>Retry</Button>}
        />
      </div>
    );
  }

  if (level.nodes.length === 0) {
    return (
      <div>
        {trail}
        <StateBlock
          title={`Nothing in ${nodeLabel(cursor)}`}
          hint="Write a note here with New note, or an agent will leave one as it works."
        />
      </div>
    );
  }

  const shown = level.nodes.slice(0, DRILL_MAX_ROWS);
  const hidden = level.nodes.length - shown.length;

  return (
    <div>
      {trail}
      <div className="ws-memory__level" role="list">
        {shown.map((node) => {
          const parsed = parseMemoryNode(node.id);
          if (!parsed) return null;
          const isBranchRow = node.kind === "branch";
          const Icon = isBranchRow ? (node.icon === "scope" ? Layers : Folder) : FileText;
          return (
            <button
              key={node.id}
              type="button"
              role="listitem"
              className={"ws-memory__row" + (node.id === selectedId ? " is-selected" : "")}
              onClick={() => (isBranchRow ? onCursor(parsed) : onSelect(parsed))}
            >
              <Icon size={16} strokeWidth={1.8} aria-hidden />
              <span className="ws-memory__rowlabel">{node.label}</span>
            </button>
          );
        })}
      </div>
      {hidden > 0 && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-2)" }}>
          <MoreHorizontal size={13} strokeWidth={1.8} aria-hidden /> {hidden} more not shown. Open a
          narrower scope to see them.
        </p>
      )}
    </div>
  );
}
