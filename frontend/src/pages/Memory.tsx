import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  RefreshCw, Plus, Trash2, FileText, FolderTree, ChevronUp,
  ShieldCheck, Lock, Globe, GitBranch,
} from "lucide-react";
import { Button, Card, CardGrid, PageHeader, SectionLabel, Row, Stack, Segmented, Select, Input, Textarea, Field, Modal, ConfirmDialog, StateBlock, Skeleton, StatusDot, Table, TableScroll, Tag, Checkbox, FileDrill, catFor, drillCursor, drillTrail, type FileDrillTrail, type SegmentedOption, shortHttpError, timeAbsolute, formatBytes } from "@workshop/ui";
import { scopeLabel } from "../lib/tasks";
import { orderScopes } from "../lib/devMode";
import { useIsWide } from "../lib/useMediaQuery";
import AttachmentViewer from "../components/chat/AttachmentViewer";
import { useDirectoryPage } from "../components/shell/directory";

import {
  getPolicy,
  setItem as setItemShare,
  chatItemRef,
  effectiveShare,
  SHARE_VALUES,
  SHARE_LABEL,
  SHARE_HELP,
  type MemoryPolicy,
  type ShareValue,
} from "../lib/memoryPolicy";

import { getJson } from "./memory/api";
import {
  drillAncestorsFor,
  encodeMemoryNode,
  isBranch,
  nodeForParams,
  paramsForNode,
  parseMemoryNode,
  searchDocs,
  type DocRow,
  type MemoryNode,
  type NoteRow,
  type ScopeNode,
} from "./memory/memoryTree";
import { MEMORY_ROOT_NODE, MemoryTreeView, createMemorySource } from "./memory/MemoryTree";
import { DocDocument, NoteDocument } from "./memory/MemoryDocument";
import { NOTE_TYPES, freshNoteId, noteTypeLabel, type NoteType } from "./memory/notes";

// The Memory surface (memory-and-documentation-model-2026-08-14.md sec 11),
// rebuilt on the file-viewer/editor design of 2026-08-22.
//
// WHAT CHANGED, AND WHY IT IS NOT A SMALLER CHANGE THAN IT LOOKS:
//
// 1. MEMORY RENDERS MARKDOWN. It did not. Both bodies on this page went into a
//    paragraph with `white-space: pre-wrap`, so a heading was a hash and a word
//    and a table was a wall of pipes, on the one page whose whole content is
//    markdown. The kit has had a `Markdown` primitive the whole time and six
//    other pages import it.
//
// 2. A DOCUMENT HAS TWO FORMS. A note and a doc open RENDERED, with an Edit
//    control that switches to the kit editor showing the SOURCE. That is
//    file-viewer-and-editor-2026-08-22.md section 4's rule, and markdown is
//    exactly the case it is about. It lives in ./memory/MemoryDocument.tsx.
//
// 3. NAVIGATION IS A TREE, NOT A GRID OF CARDS. The scope tree and the docs
//    tree are one tree through one adapter over the kit's `FileTree`
//    (./memory/MemoryTree.tsx). On a desktop it is drawn in column two by the
//    shell's directory takeover (navigation-shell-2026-08-20.md sections 3.4 and
//    8.1, built for this); on a phone it is this page's own content and drills
//    in place with `Up`, and it does not touch the tab bar.
//
//    THE PHONE SHAPE IS THE KIT'S, not Memory's. Memory shipped its own
//    `MemoryDrill` on 2026-08-22, the same day `FileDrill` landed in the kit
//    doing the same job; the copy is deleted and this page renders the kit
//    component over the SAME adapter its desktop tree reads. Two implementations
//    of one navigator is exactly how the Files tab ended up stacking a tree
//    above a file on a phone while this page did not.
//
//    A NOTE IS STILL A SCOPE AND AN ID. The tree did not turn Memory into a
//    filesystem: `?scope=&note=` and `?path=` are still the addresses, which is
//    also why every link services/refs.py has handed out still opens.
//
// 4. CHATS ARE THEIR OWN TAB. A chat is a collection that resolves by query,
//    not a document in a directory (navigation-shell sec 3.5: "A directory gets
//    the takeover and the chevron. A collection is a page and never a tree."),
//    so it moved out of the unified list rather than into the tree. Nothing was
//    dropped: the list and its sharing control are unchanged, one tab across.
//
// 5. SEARCH IS A LIST. A query is not containment, so its results are a plain
//    list in the content pane and never a second tree.
//
// The Vault and Files tabs below are untouched.

// A helper for the `${r.status} ${await r.text()}` -> shortHttpError swap: a
// small, throwaway wrapper so every fetch call site below reads the same way
// rather than repeating the `.catch(() => "")` dance six times.
async function httpFail(r: Response): Promise<Error> {
  return new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

// A thread summary (GET /api/chat/threads).
type ChatSummary = {
  id: string;
  title: string;
  context_key: string;
  scope?: string;
  created_at?: string | null;
  last_user_at?: string | null;
  archived_at?: string | null;
};

// One note in the ~/notes vault, served through the Store door
// (POST /api/stores/notes:vault/read). The vault is a plain folder of Markdown a
// real Obsidian opens; the Store URI is what makes it addressable by a grant.
type VaultNote = { path: string; title: string; size?: number; modified?: number };
// What the door returns. `tier` is "operator" for us: the operator is served
// ungated, verbatim, with no policy consulted and no broker call. The gate this
// page's data passes through is on an agent job's perimeter, not on the human.
type StoreRead = {
  uri: string;
  verb: string;
  mode: string;
  tier: string;
  items: VaultNote[];
  count: number;
};

// A file the operator attached to a chat (GET /api/chat/attachments).
//
// Files live HERE rather than on a page of their own because this page is
// already "what this system knows, by scope": notes are the text half and an
// attached image or PDF is the binary half. Same scope vocabulary, same filter,
// same four list states.
type StoredFile = {
  id: string;
  name: string;
  kind: "image" | "pdf" | "text";
  size: number;
  scope: string;
  thread_id: string;
  created_at: string;
  // The index has a row but the bytes are gone. Shown rather than hidden: a
  // file that cannot be opened should say so where you go looking for it.
  missing: boolean;
  // A converted binary (docx/xlsx/pptx/archive): the row stores a text
  // rendering AND the original bytes. `source` is its real type ("Excel
  // spreadsheet"), shown instead of the bare "text" kind.
  converted?: boolean;
  source?: string;
};
type FilesResp = { files: StoredFile[]; count: number; scopes: string[] };

const OVERFLOW_AT = 100;

type Tab = "library" | "chats" | "vault" | "files";
const TABS: SegmentedOption<Tab>[] = [
  { value: "library", label: "Library" },
  { value: "chats", label: "Chats" },
  { value: "vault", label: "Vault" },
  { value: "files", label: "Files" },
];

// Every scope `root`'s descendants, per the tree endpoint: the frontend half of
// "everything below it" (sec 4.2's `reach`). Notes are filtered this way
// server-side; chats have no such endpoint, so the same containment is
// recomputed here from the same tree data and applied client-side.
function reachSetFrom(nodes: ScopeNode[], root: string): Set<string> {
  const children = new Map<string, string[]>();
  for (const n of nodes) {
    if (n.parent) {
      const arr = children.get(n.parent) ?? [];
      arr.push(n.key);
      children.set(n.parent, arr);
    }
  }
  const out = new Set<string>([root]);
  const frontier = [root];
  while (frontier.length) {
    const k = frontier.pop()!;
    for (const c of children.get(k) ?? []) {
      if (!out.has(c)) {
        out.add(c);
        frontier.push(c);
      }
    }
  }
  return out;
}

// ---- Page ------------------------------------------------------------------

export default function Notes() {
  const [searchParams] = useSearchParams();
  // Honour ?tab=. `?tab=docs` is a still-live deep link (routes/refs.py's doc
  // resolver hands it out) from before Notes and Documents merged; it now just
  // means "open the Library", where its `?path=` selects the document.
  const urlTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    urlTab === "vault" || urlTab === "files" || urlTab === "chats" ? (urlTab as Tab) : "library",
  );

  return (
    <div>
      <PageHeader
        title="Memory"
        sub="Notes and documents by scope, plus the chats, the vault and everything attached. What agents read and write."
      />
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Memory view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "vault" ? (
        <VaultNotes />
      ) : tab === "files" ? (
        <Files />
      ) : tab === "chats" ? (
        <Chats />
      ) : (
        <Library />
      )}
    </div>
  );
}

// ---- Library: the tree, and the document it opens --------------------------

/** Rows a phone level draws before the rest becomes one line. The fourth list
 *  state: a scope with 4,000 notes reports itself instead of freezing the tab. */
const DRILL_MAX_ROWS = 100;

function Library() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const isWide = useIsWide();
  // Bumped by every write. A new source means a new adapter, which is how the
  // tree is told to drop what it had loaded and read the level again.
  const [generation, setGeneration] = useState(0);
  const source = useMemo(() => createMemorySource(), [generation]);
  const [scopeTree, setScopeTree] = useState<ScopeNode[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let live = true;
    source.scopes().then(
      (s) => { if (live) setScopeTree(s); },
      // Not swallowed: the tree asks for the same response and reports the
      // failure with a Retry. This copy is only used to answer "what contains
      // what", and an empty answer degrades to a flat tree rather than a crash.
      () => { if (live) setScopeTree([]); },
    );
    return () => { live = false; };
  }, [source]);

  const selected = useMemo(
    () => nodeForParams({
      scope: searchParams.get("scope"),
      note: searchParams.get("note"),
      path: searchParams.get("path"),
    }),
    [searchParams],
  );
  const applied = (searchParams.get("q") ?? "").trim();
  const [query, setQuery] = useState(applied);

  // THE PHONE'S TRAIL: which level the kit's drill is listing, and the way back
  // out of it. It lives here rather than in the drill so that opening a document
  // and coming back up does not lose your place, which is what `Up` is for.
  const [trail, setTrail] = useState<FileDrillTrail>(() => drillTrail(MEMORY_ROOT_NODE));

  const setParams = useCallback(
    (mutate: (next: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams);
      mutate(next);
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );

  const select = useCallback(
    (node: MemoryNode) => {
      setNotice(null);
      const params = paramsForNode(node);
      // A branch is a container. Clicking one opens it; it does not become the
      // thing on screen, and it must not blank the document you are reading.
      if (!params || isBranch(node)) return;
      setParams((next) => {
        next.delete("note");
        next.delete("path");
        next.delete("scope");
        next.delete("q");
        for (const [k, v] of Object.entries(params)) next.set(k, v);
      });
    },
    [setParams],
  );

  const clearSelection = useCallback(() => {
    setParams((next) => { next.delete("note"); next.delete("path"); });
  }, [setParams]);

  const onChanged = useCallback((msg?: string) => {
    setNotice(msg ?? null);
    setGeneration((n) => n + 1);
  }, []);

  const onDeleted = useCallback(() => {
    clearSelection();
    setNotice("Note deleted.");
    setGeneration((n) => n + 1);
  }, [clearSelection]);

  // The desktop tree lives in column two. The page declares itself a directory
  // and portals its own tree into the slot the shell provides, so the shell
  // knows nothing about scopes or notes. Off on a phone, where the tree is this
  // page's content instead and mounting it twice would cost every request twice.
  const { host, setOpen } = useDirectoryPage({ pageId: "memory", label: "Memory", enabled: isWide });

  // Walking up from a document on a phone means going to what CONTAINS it, not
  // back through history. The trail is rebuilt from the document's own ancestry,
  // so a deep link walks up correctly on its first press without having been
  // drilled into.
  const upTrail = useMemo(
    () => (selected ? drillTrail(MEMORY_ROOT_NODE, drillAncestorsFor(selected, scopeTree)) : null),
    [selected, scopeTree],
  );
  const upAction = !isWide && upTrail ? (
    <Button
      onClick={() => { setTrail(upTrail); clearSelection(); }}
      aria-label={`Up to ${drillCursor(upTrail).label}`}
    >
      <ChevronUp aria-hidden /> Up
    </Button>
  ) : null;

  let pane: JSX.Element;
  if (applied) {
    pane = (
      <SearchResults
        source={source}
        query={applied}
        onSelect={select}
        onClear={() => { setQuery(""); setParams((next) => next.delete("q")); }}
      />
    );
  } else if (selected?.kind === "note") {
    pane = (
      <NoteDocument
        key={`${selected.scope}/${selected.id}`}
        scope={selected.scope}
        noteId={selected.id}
        onChanged={() => onChanged()}
        onDeleted={onDeleted}
        actions={upAction}
      />
    );
  } else if (selected?.kind === "doc") {
    pane = (
      <DocDocument
        key={selected.path}
        path={selected.path}
        onChanged={() => onChanged("Saved. Not committed, so commit it yourself when ready.")}
        actions={upAction}
      />
    );
  } else if (!isWide) {
    // The tree IS the page here: one level, no rail, no nested scroller. Same
    // adapter the desktop tree reads, same component the Files tab drills with.
    pane = (
      <FileDrill
        adapter={source.adapter}
        trail={trail}
        onTrail={setTrail}
        // UP AT THE ROOT LEAVES THE DIRECTORY (mockup spec section 4.1). There
        // is no level above Memory's root, so the way out is off this page: the
        // Dashboard, which is the first row of this place's page column
        // (components/shell/places.ts).
        onExit={() => navigate("/")}
        selectedId={selected ? encodeMemoryNode(selected) : null}
        onSelect={(node) => {
          const parsed = parseMemoryNode(node.id);
          if (parsed) select(parsed);
        }}
        maxRows={DRILL_MAX_ROWS}
        label="Memory"
        emptyTitle="Nothing in Memory"
        emptyHint="Write a note here with New note, or an agent will leave one as it works."
      />
    );
  } else {
    pane = (
      <StateBlock
        icon={<FolderTree aria-hidden />}
        title={selected?.kind === "scope" ? `Nothing open in ${scopeLabel(selected.scope)}` : "Nothing open"}
        hint="Open the tree to pick a note or a document, or write a new note."
        action={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button size="sm" onClick={() => setOpen(true)}><FolderTree aria-hidden /> Open the tree</Button>
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}><Plus aria-hidden /> New note</Button>
          </Row>
        }
      />
    );
  }

  return (
    <div>
      {host && createPortal(
        <MemoryTreeView source={source} selected={selected} onSelect={select} />,
        host,
      )}
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end", marginBottom: "var(--space-4)" }}>
        <Field label="Search" inline>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setNotice(null);
              setParams((next) => {
                if (query.trim()) next.set("q", query.trim());
                else next.delete("q");
              });
            }}
            style={{ display: "flex", gap: "var(--space-2)" }}
          >
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find in notes and documents"
              aria-label="Search Memory"
            />
            <Button size="sm" type="submit">Search</Button>
          </form>
        </Field>
        <Button size="sm" onClick={() => setGeneration((n) => n + 1)}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
        <Button size="sm" variant="primary" onClick={() => { setNotice(null); setCreating(true); }}>
          <Plus aria-hidden /> New note
        </Button>
      </Row>
      {notice && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
      )}
      {pane}
      {creating && (
        <CreateNote
          scopes={scopeTree.map((n) => n.key)}
          initialScope={selected?.kind === "note" ? selected.scope : undefined}
          onClose={() => setCreating(false)}
          onCreated={(scope, id, msg) => {
            setCreating(false);
            setGeneration((n) => n + 1);
            setNotice(msg);
            setParams((next) => {
              next.delete("path");
              next.delete("q");
              next.set("scope", scope);
              next.set("note", id);
            });
          }}
        />
      )}
    </div>
  );
}

// ---- Search results --------------------------------------------------------
// A query is not containment, so this is a list and never a tree. Notes are
// filtered server-side by `GET /api/notes?q=` (which delegates to
// knowledge.search); documents have no search route, so their titles and paths
// are matched against the index the tree already holds.

function SearchResults({
  source,
  query,
  onSelect,
  onClear,
}: {
  source: ReturnType<typeof createMemorySource>;
  query: string;
  onSelect: (node: MemoryNode) => void;
  onClear: () => void;
}) {
  const [notes, setNotes] = useState<NoteRow[] | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setNotes(null);
    setDocs(null);
    setErr(null);
    Promise.all([
      getJson<{ notes?: NoteRow[] }>(`/api/notes?q=${encodeURIComponent(query)}`).then((j) => j.notes ?? []),
      source.docs(),
    ]).then(
      ([n, d]) => { if (live) { setNotes(n); setDocs(d); } },
      (e: unknown) => { if (live) setErr(e instanceof Error ? e.message : String(e)); },
    );
    return () => { live = false; };
  }, [source, query, attempt]);

  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't search Memory"
        hint={err}
        action={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
      />
    );
  }
  if (!notes || !docs) {
    return (
      <Stack gap="sm">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} width="70%" />)}
      </Stack>
    );
  }

  const docHits = searchDocs(docs, query);
  const total = notes.length + docHits.length;
  if (total === 0) {
    return (
      <StateBlock
        title="Nothing matches"
        hint={<>Nothing in notes or documents matches <strong>{query}</strong>. Clear the search to browse the tree instead.</>}
        action={<Button size="sm" onClick={onClear}>Clear search</Button>}
      />
    );
  }

  const noteRows = notes.slice(0, OVERFLOW_AT);
  const docRows = docHits.slice(0, Math.max(0, OVERFLOW_AT - noteRows.length));
  const hidden = total - noteRows.length - docRows.length;

  return (
    <Stack gap="lg">
      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
          {total} {total === 1 ? "result" : "results"} for {query}
        </span>
        <Button size="sm" onClick={onClear}>Clear search</Button>
      </Row>
      <div className="ws-memory__level" role="list">
        {noteRows.map((n) => (
          <button
            key={`note:${n.scope}:${n.id}`}
            type="button"
            role="listitem"
            className="ws-memory__row"
            onClick={() => onSelect({ kind: "note", scope: n.scope, id: n.id })}
          >
            <FileText size={16} strokeWidth={1.8} aria-hidden />
            <span className="ws-memory__rowlabel">{n.title || n.id}</span>
            <span className="ws-memory__more"><Tag hue={catFor(n.scope)}>{scopeLabel(n.scope)}</Tag></span>
          </button>
        ))}
        {docRows.map((d) => (
          <button
            key={`doc:${d.path}`}
            type="button"
            role="listitem"
            className="ws-memory__row"
            onClick={() => onSelect({ kind: "doc", path: d.path })}
          >
            <FileText size={16} strokeWidth={1.8} aria-hidden />
            <span className="ws-memory__rowlabel">{d.title || d.path}</span>
            <span className="ws-memory__more">{d.path}</span>
          </button>
        ))}
      </div>
      {hidden > 0 && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
          Showing the first {OVERFLOW_AT} of {total}. Narrow the search to see the rest.
        </p>
      )}
    </Stack>
  );
}

// ---- Chats -----------------------------------------------------------------
// A collection, resolved by query, so it is a page and never a tree
// (navigation-shell-2026-08-20.md sec 3.5). It used to sit in the unified
// Library list; the list became a tree, and a chat is not a document in a
// directory, so it moved here rather than being dropped.

function Chats() {
  const [threads, setThreads] = useState<ChatSummary[] | null>(null);
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  const [scopeTree, setScopeTree] = useState<ScopeNode[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/chat/threads");
      if (!r.ok) throw await httpFail(r);
      setThreads(await r.json());
      // Best effort and deliberately not awaited into the failure path above:
      // the list is still readable without it.
      void getPolicy().then(setPolicy).catch(() => {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    fetch("/api/notes/scopes/tree")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopeTree(j.scopes ?? []))
      .catch(() => setScopeTree([]));
  }, []);

  const scopeOptions = useMemo(
    () => orderScopes(scopeTree.map((n) => n.key)).map((key) => {
      const node = scopeTree.find((n) => n.key === key);
      return { key, label: "  ".repeat(node?.depth ?? 0) + scopeLabel(key) };
    }),
    [scopeTree],
  );

  const reachSet = useMemo(
    () => (scope === "all" ? null : reachSetFrom(scopeTree, scope)),
    [scope, scopeTree],
  );

  const q = applied.trim().toLowerCase();
  const items = (threads ?? [])
    .filter((c) => !c.archived_at)
    .filter((c) => !q || c.title.toLowerCase().includes(q))
    .filter((c) => !reachSet || reachSet.has(c.scope || "workshop"))
    .sort((a, b) => {
      const ak = a.last_user_at || a.created_at || "";
      const bk = b.last_user_at || b.created_at || "";
      return ak < bk ? 1 : ak > bk ? -1 : 0;
    });
  const overflow = items.length > OVERFLOW_AT;
  const shown = overflow ? items.slice(0, OVERFLOW_AT) : items;
  const filtered = scope !== "all" || applied.trim() !== "";

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load chats"
        hint={<><code>/api/chat/threads</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!threads) {
    body = (
      <CardGrid>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <Stack gap="sm">
              <Skeleton width="60%" />
              <Skeleton width="40%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (items.length === 0) {
    body = (
      <StateBlock
        title={filtered ? "No chats match" : "No chats yet"}
        hint={
          filtered
            ? <>Reset <strong>Scope</strong> to <strong>All</strong>, or clear the search.</>
            : "Start a conversation and it shows up here, with what other chats may find in it."
        }
        action={filtered ? <Button size="sm" onClick={() => { setScope("all"); setQuery(""); setApplied(""); }}>Clear filters</Button> : undefined}
      />
    );
  } else {
    body = (
      <>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginBottom: "var(--space-3)" }}>
            Listing the first {OVERFLOW_AT} of {items.length}. Narrow by scope or search to see the rest.
          </p>
        )}
        <CardGrid className="ws-notes-grid">
          {shown.map((c) => (
            <li key={c.id}>
              <Link
                to={`/chat/${encodeURIComponent(c.context_key)}?thread=${encodeURIComponent(c.id)}`}
                className="ws-note-card__link"
              >
                <Card interactive compact className="ws-note-card">
                  <Stack gap="sm">
                    <span className="ws-note-card__title">{c.title || "Chat"}</span>
                    <Row wrap className="ws-note-card__footer" style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <Tag hue={catFor(c.scope || "workshop")}>{scopeLabel(c.scope)}</Tag>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                        {timeAbsolute(c.last_user_at || c.created_at)}
                      </span>
                    </Row>
                  </Stack>
                </Card>
              </Link>
              {/* Outside the Link on purpose: see ChatShareBadge below. */}
              <ChatShareBadge
                threadId={c.id}
                scope={c.scope || "workshop"}
                title={c.title || "Chat"}
                policy={policy}
                onSaved={setPolicy}
              />
            </li>
          ))}
        </CardGrid>
      </>
    );
  }

  return (
    <div>
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end", marginBottom: "var(--space-4)" }}>
        <Field label="Scope" inline>
          <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Filter chats by scope">
            <option value="all">All scopes</option>
            {scopeOptions.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Search" inline>
          <form
            onSubmit={(e) => { e.preventDefault(); setApplied(query); }}
            style={{ display: "flex", gap: "var(--space-2)" }}
          >
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find by title"
              aria-label="Search chats"
            />
            <Button size="sm" type="submit">Search</Button>
          </form>
        </Field>
        <Button size="sm" onClick={refresh} loading={loading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      </Row>
      {body}
    </div>
  );
}

// ---- Sharing ---------------------------------------------------------------
// One chat's entry in the sharing model (sec 6). A full "Other chats find it:
// <select> <help sentence>" row used to sit as a SIBLING of the card inside the
// grid cell, which broke two ways at once: as a second flex child of
// `.ws-notes-grid > li` it fought the card for the row's width, and repeating
// the full sentence on every one of a hundred cards was noise even where it fit.
//
// This is a compact icon badge instead, living in the card's footer, that opens
// a small dialog on click. It sits OUTSIDE the card's `Link` (a select nested
// inside a link is not operable, and a nested <button> inside an <a> is invalid
// markup besides) as an absolutely-positioned overlay in the corner:
// absolutely-positioned children of a flex container are not flex items, so it
// costs the grid's single-child stretch rule nothing.
//
// CURATION, NOT SECURITY. This changes what other chats' searches turn up. It
// does not restrict what anything can read: everything here runs as one uid.
// Per SEC-A2, do not relabel this permission or access.
const SHARE_ICON: Record<ShareValue, typeof Lock> = {
  inherit: GitBranch,
  open: Globe,
  private: Lock,
};

function ChatShareBadge({
  threadId, scope, title, policy, onSaved,
}: {
  threadId: string;
  scope: string;
  title: string;
  policy: MemoryPolicy | null;
  onSaved: (p: MemoryPolicy) => void;
}) {
  const ref = chatItemRef(threadId);
  const stored = effectiveShare(policy, "chat", scope, ref);
  const [open, setOpen] = useState(false);
  const Icon = SHARE_ICON[stored];
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        icon
        className="ws-note-card__share"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(true); }}
        aria-label={`Sharing with other chats: ${SHARE_LABEL[stored]}. ${SHARE_HELP[stored]} Click to change.`}
        title={`Other chats find it: ${SHARE_LABEL[stored]}`}
      >
        <Icon size={13} strokeWidth={1.8} aria-hidden />
      </Button>
      {open && (
        <ChatShareDialog
          threadId={threadId}
          scope={scope}
          title={title}
          policy={policy}
          onSaved={(p) => { onSaved(p); setOpen(false); }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

// The dialog the badge opens. Explicit Save rather than commit-on-change, per
// frontend/CLAUDE.md rule 7: this writes config.
function ChatShareDialog({
  threadId, scope, title, policy, onSaved, onClose,
}: {
  threadId: string;
  scope: string;
  title: string;
  policy: MemoryPolicy | null;
  onSaved: (p: MemoryPolicy) => void;
  onClose: () => void;
}) {
  const ref = chatItemRef(threadId);
  const stored = effectiveShare(policy, "chat", scope, ref);
  const [value, setValue] = useState<ShareValue>(stored);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = value !== stored;

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      onSaved(await setItemShare(ref, value));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }, [ref, value, onSaved]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`Sharing: ${title || "Chat"}`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!dirty} loading={saving} onClick={() => { void save(); }}>
            Save
          </Button>
        </>
      }
    >
      <Stack gap="lg">
        <Field label="Other chats find it" htmlFor="chat-share-select" help={SHARE_HELP[value]}>
          <Select
            id="chat-share-select"
            value={value}
            onChange={(e) => setValue(e.target.value as ShareValue)}
            data-autofocus
          >
            {SHARE_VALUES.map((v) => (
              <option key={v} value={v}>{SHARE_LABEL[v]}</option>
            ))}
          </Select>
        </Field>
        {err && <p className="ws-field__error">{err}</p>}
      </Stack>
    </Modal>
  );
}

// ---- Create ----------------------------------------------------------------

function CreateNote({
  scopes,
  initialScope,
  onClose,
  onCreated,
}: {
  scopes: string[];
  initialScope?: string;
  onClose: () => void;
  onCreated: (scope: string, id: string, msg: string) => void;
}) {
  const options = scopes.length ? scopes : ["workshop"];
  const [scope, setScope] = useState<string>(initialScope ?? options[0]);
  const [title, setTitle] = useState("");
  const [hook, setHook] = useState("");
  const [content, setContent] = useState("");
  const [noteType, setNoteType] = useState<NoteType>("fact");
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    if (!title.trim() && !content.trim()) { setErr("A title or body is required."); return; }
    setBusy(true);
    setErr(null);
    try {
      const id = freshNoteId(title);
      const r = await fetch(
        `/api/knowledge/${encodeURIComponent(scope)}/notes/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            title: title.trim(), content, hook: hook.trim(),
            author: "operator", note_type: noteType, pinned,
          }),
        },
      );
      if (!r.ok) throw await httpFail(r);
      onCreated(scope, id, `Note saved to ${scopeLabel(scope)}.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [scope, title, hook, content, noteType, pinned, onCreated]);

  return (
    <Modal
      open
      onClose={onClose}
      title="New note"
      size="lg"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={submit}>Save note</Button>
        </>
      }
    >
      <Stack gap="lg">
        <Field label="Scope" htmlFor="new-note-scope">
          <Select id="new-note-scope" value={scope} onChange={(e) => setScope(e.target.value)} data-autofocus>
            {options.map((s) => (
              <option key={s} value={s}>{scopeLabel(s)}</option>
            ))}
          </Select>
        </Field>
        <Field label="Title" htmlFor="new-note-title">
          <Input
            id="new-note-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What this note is"
          />
        </Field>
        <Field label="Hook" htmlFor="new-note-hook" optional help="A one-line summary shown in the index and lists.">
          <Input
            id="new-note-hook"
            value={hook}
            onChange={(e) => setHook(e.target.value)}
            placeholder="One-line summary (optional)"
          />
        </Field>
        <Row wrap style={{ gap: "var(--space-4)" }}>
          <Field label="Type" htmlFor="new-note-type" inline>
            <Select id="new-note-type" value={noteType} onChange={(e) => setNoteType(e.target.value as NoteType)}>
              {NOTE_TYPES.map((t) => (
                <option key={t} value={t}>{noteTypeLabel(t)}</option>
              ))}
            </Select>
          </Field>
          <Checkbox checked={pinned} onChange={setPinned} label="Pinned" />
        </Row>
        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
          Pinned notes, then preferences and decisions, rank first in what gets read into every chat in this scope.
        </p>
        <Field label="Body" htmlFor="new-note-body" optional>
          <Textarea
            id="new-note-body"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            placeholder="Markdown body"
          />
        </Field>
        {err && <p className="ws-field__error">{err}</p>}
      </Stack>
    </Modal>
  );
}

// ---- Vault -----------------------------------------------------------------
// The ~/notes vault, read through the Store door rather than through a route of
// the notes app's own. That is the point: the notes app no longer owns its gate.
// Whether the vault is reachable, by whom, and how narrowly, is a policy record
// in backend/store-policies.json, keyed by URI — outside the folder it protects,
// so a `write` grant on the vault can never rewrite the vault's own gate.
//
// The operator is not gated by any of it. This page sends no identity, so the
// door serves it verbatim without consulting a policy or calling a broker; an
// agent reaching the same URI gets adjudicated once and handed a narrow grant.
function VaultNotes() {
  const [data, setData] = useState<StoreRead | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/stores/notes:vault/read", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: applied.trim()
          ? JSON.stringify({ verb: "search", selector: { query: applied.trim() } })
          : JSON.stringify({ verb: "list", selector: {} }),
      });
      if (!r.ok) throw await httpFail(r);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [applied]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const items = data?.items ?? [];
  const overflow = items.length > OVERFLOW_AT;
  const shown = overflow ? items.slice(0, OVERFLOW_AT) : items;

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't read the vault"
        hint={<><code>/api/stores/notes:vault/read</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!data) {
    body = (
      <CardGrid>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <Stack gap="sm">
              <Skeleton width="60%" />
              <Skeleton width="40%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (items.length === 0) {
    body = (
      <StateBlock
        title={applied ? "Nothing in the vault matches" : "The vault is empty"}
        hint={
          applied ? (
            <>Clear the search to list every note in <code>~/notes</code>.</>
          ) : (
            <>The vault is a plain folder of Markdown that Obsidian opens untouched. Add a note there and it shows up here.</>
          )
        }
        action={applied ? <Button size="sm" onClick={() => { setQuery(""); setApplied(""); }}>Clear search</Button> : undefined}
      />
    );
  } else {
    body = (
      <>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>
            Showing the first {OVERFLOW_AT} of {items.length}. Search to narrow the list.
          </p>
        )}
        <CardGrid className="ws-notes-grid">
          {shown.map((n) => (
            <li key={n.path}>
              <Card compact className="ws-doc-card">
                <Stack gap="sm">
                  <Row style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                    <FileText size={14} aria-hidden />
                    <span className="ws-note-card__title">{n.title || n.path}</span>
                  </Row>
                  <span className="ws-doc-card__path">{n.path}</span>
                </Stack>
              </Card>
            </li>
          ))}
        </CardGrid>
      </>
    );
  }

  return (
    <Stack gap="lg">
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
        <Field label="Search" inline>
          <form
            onSubmit={(e) => { e.preventDefault(); setApplied(query); }}
            style={{ display: "flex", gap: "var(--space-2)" }}
          >
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find in the vault"
              aria-label="Search the vault"
            />
            <Button size="sm" type="submit">Search</Button>
          </form>
        </Field>
        <Button size="sm" onClick={refresh} loading={loading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      </Row>
      <SectionLabel>
        <ShieldCheck size={14} aria-hidden /> workshop://store/notes/vault
      </SectionLabel>
      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginTop: "calc(-1 * var(--space-2))" }}>
        You see every note in full. An agent asking for the same thing gets only what it
        has been granted, and only for as long as the grant lasts.
      </p>
      {body}
    </Stack>
  );
}

// ---- Files -----------------------------------------------------------------
// Everything the operator has attached to a chat, newest first. A table rather
// than the card grid the other tabs use: a file is a row of small facts (name,
// kind, size, scope, when), and cards would spend a whole tile saying very
// little. It is the same four list states and the same scope filter either way.
function Files() {
  const [searchParams, setSearchParams] = useSearchParams();
  // ?file=<id> arrives from the attachment viewer's "Open in Memory". It narrows
  // the table to that one file rather than opening its viewer again: you just
  // came from that viewer, so re-opening it would be a loop. Narrowing is also
  // the only thing that reliably WORKS, since the row could otherwise be a
  // hundred entries down a list you would then have to hunt through.
  const wantFile = searchParams.get("file");
  const [data, setData] = useState<FilesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  // Deleting bytes is destructive and gets a ConfirmDialog naming the file, per
  // the design system. Nothing here is one-tap.
  const [confirm, setConfirm] = useState<StoredFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [viewing, setViewing] = useState<StoredFile | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (scope !== "all") params.set("scope", scope);
      if (applied.trim()) params.set("q", applied.trim());
      const qs = params.toString();
      const r = await fetch(`/api/chat/attachments${qs ? `?${qs}` : ""}`);
      if (!r.ok) throw await httpFail(r);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, applied]);

  useEffect(() => { refresh(); }, [refresh]);

  const remove = useCallback(async (f: StoredFile) => {
    setBusy(true);
    try {
      const r = await fetch(`/api/chat/attachments/${encodeURIComponent(f.id)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw await httpFail(r);
      setConfirm(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setConfirm(null);
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const all = data?.files ?? [];
  const files = wantFile ? all.filter((f) => f.id === wantFile) : all;
  const overflow = files.length > OVERFLOW_AT;
  const shown = overflow ? files.slice(0, OVERFLOW_AT) : files;
  // Scope options come from the FILES, not the notes scope list: a scope with no
  // attachment in it would only ever filter to an empty table.
  const scopeOptions = data?.scopes ?? [];

  const filterBar = (
    <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
      <Field label="Scope" inline>
        <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Filter files by scope">
          <option value="all">All scopes</option>
          {scopeOptions.map((s) => (
            <option key={s} value={s}>{scopeLabel(s)}</option>
          ))}
        </Select>
      </Field>
      <Field label="Search" inline>
        <form
          onSubmit={(e) => { e.preventDefault(); setApplied(query); }}
          style={{ display: "flex", gap: "var(--space-2)" }}
        >
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find by filename"
            aria-label="Search files"
          />
          <Button size="sm" type="submit">Search</Button>
        </form>
      </Field>
      <Button size="sm" onClick={refresh} loading={loading}>
        <RefreshCw aria-hidden /> Refresh
      </Button>
    </Row>
  );

  const filtered = scope !== "all" || applied.trim() !== "" || !!wantFile;

  // Drops ?file= and leaves every other param alone, so clearing the deep link
  // does not also clear a scope the operator picked after landing.
  const clearWanted = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("file");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load files"
        hint={<><code>/api/chat/attachments</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!data) {
    body = (
      <Stack gap="sm">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} />)}
      </Stack>
    );
  } else if (files.length === 0) {
    body = filtered ? (
      <StateBlock
        title={wantFile ? "That file is no longer here" : "No files match"}
        hint={wantFile
          ? "It may have been deleted since the link was made."
          : "Clear the scope filter or the search to see everything attached."}
        action={
          <Button
            size="sm"
            onClick={() => { setScope("all"); setQuery(""); setApplied(""); clearWanted(); }}
          >
            {wantFile ? "Show all files" : "Clear filters"}
          </Button>
        }
      />
    ) : (
      <StateBlock
        title="No files yet"
        hint="Drop an image or a file into any chat, or use the paperclip beside the composer. What you attach is stored here."
      />
    );
  } else {
    body = (
      <>
        {wantFile && (
          <Row style={{ gap: "var(--space-2)", alignItems: "center", marginBottom: "var(--space-3)" }}>
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
              Showing one file.
            </span>
            <Button size="sm" onClick={clearWanted}>Show all files</Button>
          </Row>
        )}
        <TableScroll>
          <Table>
            <thead>
              <tr>
                <th>File</th>
                <th>Kind</th>
                <th>Size</th>
                <th>Scope</th>
                <th>Attached</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {shown.map((f) => (
                <tr key={f.id}>
                  <td>
                    {f.missing ? (
                      <span>{f.name}</span>
                    ) : (
                      // Opens the viewer over this table, not a new window. The
                      // link version was a dead end on mobile (see
                      // chat/AttachmentViewer) and it also cost the operator
                      // their scope filter and scroll position on the way back.
                      <button type="button" className="chat-att-link" onClick={() => setViewing(f)}>
                        {f.name}
                      </button>
                    )}
                  </td>
                  <td>
                    {f.missing
                      ? <StatusDot status="warn">Bytes missing</StatusDot>
                      : <Tag hue={catFor(f.source || f.kind)}>{f.converted && f.source ? f.source : f.kind}</Tag>}
                  </td>
                  <td>{formatBytes(f.size)}</td>
                  <td>
                    {f.scope
                      ? <Tag hue={catFor(f.scope)}>{scopeLabel(f.scope)}</Tag>
                      : <span className="ws-muted">unscoped</span>}
                  </td>
                  <td>{timeAbsolute(f.created_at)}</td>
                  <td>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirm(f)}
                      aria-label={`Delete ${f.name}`}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableScroll>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginTop: "var(--space-3)" }}>
            Showing the {OVERFLOW_AT} newest of {files.length}. Narrow it with the scope filter or a search.
          </p>
        )}
      </>
    );
  }

  return (
    <>
      <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
      {body}
      {viewing && (
        <AttachmentViewer
          file={{ id: viewing.id, name: viewing.name, kind: viewing.kind,
                  converted: viewing.converted, source: viewing.source }}
          onClose={() => setViewing(null)}
          inMemory
        />
      )}
      {confirm && (
        <ConfirmDialog
          open
          title="Delete this file?"
          // Names the consequence, and the one thing that is easy to get wrong
          // here: the chat keeps the message, it just loses what was attached.
          body={`${confirm.name} is deleted for good. The chat that used it keeps the message but can no longer open the file.`}
          confirmLabel="Delete"
          busy={busy}
          onConfirm={() => { void remove(confirm); }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  );
}

