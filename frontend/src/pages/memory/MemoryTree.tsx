// Memory's tree: the adapter that turns three endpoints into levels, and the
// desktop tree drawn over it.
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
// TWO SHAPES, ONE ADAPTER, AND NO SECOND IMPLEMENTATION. On a desktop the tree
// is the kit's `FileTree`, drawn in column two by the shell takeover. On a phone
// it is the kit's `FileDrill`, which is the page's own CONTENT and walks one
// level at a time with `Up` (navigation-shell-2026-08-20.md section 4.4: "Up is
// structural and belongs to the page. Back is history and belongs to the
// browser."). Memory built its own drill on 2026-08-22, the same day the kit
// gained one; that copy is gone and Memory.tsx renders the kit component over
// the SAME adapter, so a level cannot mean two things.
import { useEffect, useState } from "react";
import {
  FileTree,
  Skeleton,
  type FileTreeAdapter,
  type FileTreeLevel,
  type FileTreeNode,
} from "@workshop/ui";
import { getJson } from "./api";
import {
  DOCS_ROOT,
  baseName,
  childScopes,
  docLevel,
  drillAncestorsFor,
  encodeMemoryNode,
  memoryFileNode,
  notesInScope,
  parseMemoryNode,
  type DocRow,
  type MemoryNode,
  type NoteRow,
  type ScopeNode,
} from "./memoryTree";

function scopeNode(key: string): FileTreeNode {
  return memoryFileNode({ kind: "scope", scope: key });
}

/** A note's row. The stored title is what a reader recognises, so it overrides
 *  the id `memoryFileNode` can name it by; the address is unchanged. */
function noteNode(note: NoteRow): FileTreeNode {
  return { ...memoryFileNode({ kind: "note", scope: note.scope, id: note.id }), label: note.title || note.id };
}

function docDirNode(path: string): FileTreeNode {
  return memoryFileNode({ kind: "docdir", path });
}

function docNode(doc: DocRow): FileTreeNode {
  return { ...memoryFileNode({ kind: "doc", path: doc.path }), label: doc.title || baseName(doc.path) };
}

const DOCS_NODE: FileTreeNode = memoryFileNode({ kind: "docs" });

export const MEMORY_ROOT_NODE: FileTreeNode = memoryFileNode({ kind: "root" });

export type MemorySource = {
  /** For both kit primitives: `FileTree` on a desktop, `FileDrill` on a phone.
   *  One adapter, so neither shape can disagree about what a level contains. */
  adapter: FileTreeAdapter;
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

  return { adapter, scopes, docs };
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

  const defaultOpenIds = selected ? drillAncestorsFor(selected, scopeTree).map((n) => n.id) : [];

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
