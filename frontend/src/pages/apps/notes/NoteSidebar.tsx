import { useEffect, useMemo, useState } from "react";
import { Search, Folder, FileText, ChevronRight, ChevronDown } from "lucide-react";
import { Input, Stack, StateBlock, Skeleton, Button } from "../../../ui";
import { notesApi, type NoteMeta, type SearchHit } from "./api";

type TreeNode = { name: string; path: string; dir: boolean; children: TreeNode[] };

function buildTree(notes: NoteMeta[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", dir: true, children: [] };
  for (const n of notes) {
    const parts = n.path.split("/");
    let cur = root;
    let acc = "";
    parts.forEach((part, i) => {
      acc = acc ? `${acc}/${part}` : part;
      const isFile = i === parts.length - 1;
      let child = cur.children.find((c) => c.name === part && c.dir === !isFile);
      if (!child) {
        child = { name: part, path: acc, dir: !isFile, children: [] };
        cur.children.push(child);
      }
      cur = child;
    });
  }
  const sortRec = (node: TreeNode) => {
    node.children.sort((a, b) => (a.dir !== b.dir ? (a.dir ? -1 : 1) : a.name.localeCompare(b.name)));
    node.children.forEach(sortRec);
  };
  sortRec(root);
  return root.children;
}

function TreeRows({
  nodes,
  depth,
  activePath,
  expanded,
  toggle,
  onSelect,
}: {
  nodes: TreeNode[];
  depth: number;
  activePath: string | null;
  expanded: Set<string>;
  toggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const pad = { paddingLeft: `calc(var(--space-2) + ${depth} * var(--space-3))` };
        if (node.dir) {
          const open = expanded.has(node.path);
          return (
            <div key={node.path}>
              <button type="button" className="ws-note-row" style={pad} onClick={() => toggle(node.path)}>
                {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <Folder size={14} />
                <span className="ws-note-row-label">{node.name}</span>
              </button>
              {open && (
                <TreeRows
                  nodes={node.children}
                  depth={depth + 1}
                  activePath={activePath}
                  expanded={expanded}
                  toggle={toggle}
                  onSelect={onSelect}
                />
              )}
            </div>
          );
        }
        const active = node.path === activePath;
        return (
          <button
            key={node.path}
            type="button"
            className={"ws-note-row" + (active ? " is-active" : "")}
            style={pad}
            aria-current={active ? "true" : undefined}
            onClick={() => onSelect(node.path)}
          >
            <FileText size={14} />
            <span className="ws-note-row-label">{node.name.replace(/\.md$/i, "")}</span>
          </button>
        );
      })}
    </>
  );
}

export default function NoteSidebar({
  notes,
  error,
  onRetry,
  activePath,
  onSelect,
}: {
  notes: NoteMeta[] | null;
  error: string | null;
  onRetry: () => void;
  activePath: string | null;
  onSelect: (path: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const tree = useMemo(() => (notes ? buildTree(notes) : []), [notes]);
  // Expand all directories by default whenever the note set changes.
  useEffect(() => {
    if (!notes) return;
    const dirs = new Set<string>();
    const walk = (nodes: TreeNode[]) =>
      nodes.forEach((n) => {
        if (n.dir) {
          dirs.add(n.path);
          walk(n.children);
        }
      });
    walk(tree);
    setExpanded(dirs);
  }, [notes, tree]);

  const q = query.trim();
  useEffect(() => {
    if (!q) {
      setHits(null);
      setSearchErr(null);
      return;
    }
    let live = true;
    setSearchErr(null);
    notesApi
      .search(q)
      .then((r) => live && setHits(r.results))
      .catch((e) => live && setSearchErr(String(e.message || e)));
    return () => {
      live = false;
    };
  }, [q]);

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });

  return (
    <div className="ws-note-sidebar">
      <div className="ws-note-search">
        <Search size={14} />
        <Input
          value={query}
          placeholder="Search notes"
          aria-label="Search notes"
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="ws-note-list">
        {q ? (
          searchErr ? (
            <StateBlock variant="error" title="Search failed" hint={searchErr} action={<Button onClick={onRetry}>Retry</Button>} />
          ) : hits === null ? (
            <Stack gap="sm">
              <Skeleton width="90%" />
              <Skeleton width="70%" />
            </Stack>
          ) : hits.length === 0 ? (
            <StateBlock title="No matches" hint={`Nothing matched "${q}".`} />
          ) : (
            <>
              {hits.map((h, i) => (
                <button
                  key={i}
                  type="button"
                  className={"ws-note-hit" + (h.path === activePath ? " is-active" : "")}
                  onClick={() => onSelect(h.path)}
                >
                  <span className="ws-note-row-label">{h.path}</span>
                  <span className="ws-note-meta">{h.snippet}</span>
                </button>
              ))}
            </>
          )
        ) : error ? (
          <StateBlock variant="error" title="Could not load notes" hint={error} action={<Button onClick={onRetry}>Retry</Button>} />
        ) : notes === null ? (
          <Stack gap="sm">
            <Skeleton width="80%" />
            <Skeleton width="60%" />
            <Skeleton width="70%" />
          </Stack>
        ) : notes.length === 0 ? (
          <StateBlock title="No notes yet" hint="Create your first note with New note above." />
        ) : (
          <TreeRows nodes={tree} depth={0} activePath={activePath} expanded={expanded} toggle={toggle} onSelect={onSelect} />
        )}
      </div>
    </div>
  );
}
