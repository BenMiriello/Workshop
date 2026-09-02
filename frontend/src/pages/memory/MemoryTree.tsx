// Memory's tree: the adapter that turns three endpoints into levels, and the
// desktop tree drawn over it.
//
// THE ADAPTER IS THE WHOLE POINT. `FileTree` in the kit renders nodes with lazy
// children and knows nothing about HTTP, paths or scopes
// (file-viewer-and-editor-2026-08-22.md section 5.3: "a directory and the memory
// scope tree are the same component with different adapters"). This file is that
// second adapter. It reads three endpoints that already exist and adds none:
//
//   GET /api/memory/scopes/tree   the scope tree, root-first, with parents
//   GET /api/memory/notes               every note, filtered to a scope exactly, here
//   GET /api/documents           the committed docs/ index
//
// THE NAVIGATOR IS PER-PLACE (memory-is-files-2026-08-28.md §3.1). A place
// switcher above the tree selects which place's memory is shown: Workshop,
// Development, or any app/project with notes. Inside each place, system/ and
// user/ folders appear at the top as physical filesystem folders. Notes at the
// place root follow below them. "Scopes" and "Roles" were group labels on
// internal addressing concepts; they are gone.
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
  Select,
  Skeleton,
  timeAgo,
  type FileTreeAdapter,
  type FileTreeLevel,
  type FileTreeNode,
} from "@workshop/ui";
import { scopeLabel } from "../../lib/tasks";
import { getJson } from "./api";
import {
  DOCS_ROOT,
  baseName,
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

// THE META COLUMN (shell-mockup-spec-2026-08-22.md section 4.2). The phone row
// carries a CHILD COUNT on a container and a RELATIVE TIME on a document, and
// this adapter can answer both without a single extra request: the scope tree,
// the docs index and the cross-scope notes listing each describe the WHOLE
// structure in one cached response, so a count is arithmetic over what is
// already here. `{ suffix: false }` is the mockup's compact form ("2h", "5d"),
// not "2h ago": the column is 12px mono at the right edge of a 390px row.
function countOf(n: number): string {
  return String(n);
}

function folderNode(scope: string, folder: "system" | "user"): FileTreeNode {
  return memoryFileNode({ kind: "folder", scope, folder });
}

/** A note's row. The stored title is what a reader recognises, so it overrides
 *  the id `memoryFileNode` can name it by; the address is unchanged. */
function noteNode(note: NoteRow): FileTreeNode {
  const slash = note.id.indexOf("/");
  const slug = slash < 0 ? note.id : note.id.slice(slash + 1);
  return {
    ...memoryFileNode({ kind: "note", scope: note.scope, id: note.id }),
    label: note.title || slug,
    detail: timeAgo(note.updated, { suffix: false }) || undefined,
  };
}

function docDirNode(path: string, children: number): FileTreeNode {
  return { ...memoryFileNode({ kind: "docdir", path }), detail: countOf(children) };
}

function docNode(doc: DocRow): FileTreeNode {
  return {
    ...memoryFileNode({ kind: "doc", path: doc.path }),
    label: doc.title || baseName(doc.path),
    detail: timeAgo(doc.mtime, { suffix: false }) || undefined,
  };
}

/** The same, for a directory of the committed docs tree. */
function docDirCount(docs: readonly DocRow[], dir: string): number {
  const level = docLevel(docs, dir);
  return level.dirs.length + level.files.length;
}

function docsNode(docs: readonly DocRow[]): FileTreeNode {
  return {
    ...memoryFileNode({ kind: "docs" }),
    detail: countOf(docDirCount(docs, DOCS_ROOT)),
  };
}

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
 * `selectedPlace` is the scope key of the currently shown place (e.g.
 * "workshop", "development", "project:birds"). The root level shows system/,
 * user/, and the notes filed to exactly that scope. Documents are appended for
 * the workshop place only.
 *
 * The scope tree, the docs index and the notes listing are each ONE response
 * describing the whole structure, so caching them is what makes a level a
 * grouping rather than a request, and what lets every container row carry a
 * child count. Build it in a `useMemo`: a new source means a new adapter, and
 * the tree drops every level it had loaded and reloads.
 */
export function createMemorySource(selectedPlace: string = "workshop"): MemorySource {
  let scopesP: Promise<ScopeNode[]> | null = null;
  let docsP: Promise<DocRow[]> | null = null;
  let notesP: Promise<NoteRow[]> | null = null;

  const scopes = (): Promise<ScopeNode[]> => {
    if (!scopesP) {
      scopesP = getJson<{ scopes?: ScopeNode[] }>("/api/memory/scopes/tree")
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

  // EVERY NOTE, ONCE. `GET /api/memory/notes` unscoped is the same response shape the
  // per-scope call returns, and one cached copy answers every level without a
  // request each.
  const notes = (): Promise<NoteRow[]> => {
    if (!notesP) {
      notesP = getJson<{ notes?: NoteRow[] }>("/api/memory/notes")
        .then((j) => j.notes ?? [])
        .catch((e) => { notesP = null; throw e; });
    }
    return notesP;
  };

  const children = async (node: MemoryNode): Promise<FileTreeLevel> => {
    switch (node.kind) {
      case "root": {
        const [, all, allDocs] = await Promise.all([scopes(), notes(), docs()]);
        const rootNotes = notesInScope(all, selectedPlace).filter((n) => !n.id.includes("/"));
        const nodes: FileTreeNode[] = [
          folderNode(selectedPlace, "system"),
          folderNode(selectedPlace, "user"),
          ...rootNotes.map(noteNode),
        ];
        // Documents are committed repo docs: shown in the Workshop place only.
        if (selectedPlace === "workshop") {
          nodes.push(docsNode(allDocs));
        }
        return { nodes };
      }
      case "folder": {
        const all = await notes();
        const prefix = `${node.folder}/`;
        const folderNotes = notesInScope(all, node.scope).filter((n) => n.id.startsWith(prefix));
        return { nodes: folderNotes.map(noteNode) };
      }
      case "docs":
      case "docdir": {
        const all = await docs();
        const dir = node.kind === "docs" ? DOCS_ROOT : node.path;
        const level = docLevel(all, dir);
        return {
          nodes: [
            ...level.dirs.map((d) => docDirNode(d, docDirCount(all, d))),
            ...level.files.map(docNode),
          ],
        };
      }
      default:
        // A leaf (note, doc) or a scope node used for URL state only.
        // The tree never asks for a leaf's children; an empty level is
        // the right answer if something ever does.
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
  selectedPlace,
  onPlaceChange,
}: {
  source: MemorySource;
  selected: MemoryNode | null;
  onSelect: (node: MemoryNode) => void;
  selectedPlace: string;
  onPlaceChange: (place: string) => void;
}) {
  // The scope tree is needed before the primitive mounts, not after: which
  // branches start open is read ONCE by `FileTree` (a deep link into a note
  // should land with its branch already open), and ancestry is only knowable
  // from this response. It is the same cached request the root level makes, so
  // waiting for it here costs one render and no traffic.
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

  // Places are all scopes that are not role axes. The operator picks one via
  // the switcher; each has its own system/ and user/ and its own notes.
  const places = scopeTree.filter((n) => !n.key.startsWith("role:"));

  const defaultOpenIds = selected ? drillAncestorsFor(selected, scopeTree).map((n) => n.id) : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {places.length > 1 && (
        <div style={{ padding: "var(--space-2) var(--space-3)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <Select
            value={selectedPlace}
            onChange={(e) => onPlaceChange(e.target.value)}
            aria-label="Memory place"
            style={{ width: "100%" }}
          >
            {places.map((p) => (
              <option key={p.key} value={p.key}>{scopeLabel(p.key)}</option>
            ))}
          </Select>
        </div>
      )}
      <div style={{ flex: "1 1 0", minHeight: 0, overflow: "auto" }}>
        <FileTree
          className="ws-memory__tree"
          label="Memory"
          adapter={source.adapter}
          defaultOpenIds={defaultOpenIds}
          selectedId={selected ? encodeMemoryNode(selected) : null}
          emptyTitle="Nothing here yet"
          emptyHint="Write a note with New note, or an agent will leave one as it works."
          onSelect={(node) => {
            const parsed = parseMemoryNode(node.id);
            if (parsed) onSelect(parsed);
          }}
        />
      </div>
    </div>
  );
}
