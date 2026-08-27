import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import {
  CheckSquare, ChevronUp, FilePlus, FileText, FolderTree, MessageSquare,
  Plus, RefreshCw, Search,
} from "lucide-react";
import { Button, PageActionBar, Row, Stack, Select, Input, Textarea, Field, Modal, StateBlock, Skeleton, Tag, Checkbox, FileDrill, catFor, drillCursor, drillTrail, type FileDrillTrail, shortHttpError } from "@workshop/ui";
import { scopeLabel } from "../lib/tasks";
import { useIsWide } from "../lib/useMediaQuery";
import { useDirectoryPage } from "../components/shell/directory";
import { useContentTakeover, useDirectoryContent } from "../components/shell/takeover";
import { directoryChrome } from "../components/shell/pageChrome";


import {
  drillAncestorsFor,
  encodeMemoryNode,
  isBranch,
  nodeForParams,
  paramsForNode,
  parseMemoryNode,
  type MemoryNode,
  type ScopeNode,
} from "./memory/memoryTree";
import { MEMORY_ROOT_NODE, MemoryTreeView, createMemorySource } from "./memory/MemoryTree";
import { DocDocument, NoteDocument } from "./memory/MemoryDocument";
import { NOTE_TYPES, freshNoteId, noteTypeLabel, type NoteType } from "./memory/notes";

// MEMORY: the notes and documents this system knows, as one tree.
//
// WHAT THIS PAGE IS NOW, AND WHAT IT STOPPED BEING (2026-08-27, and the whole
// story is docs/platform-redesign/memory-page-and-file-viewer-2026-08-27.md).
//
// It used to be four unrelated collections behind one Segmented: this library,
// the chat list, the notes app's Markdown vault, and chat attachments. Four
// subsystems, five routers, three of them with a front door somewhere else
// already, on one page named after the one that lives here. The operator's
// read, walking it: "Why are these all separate? Why do we have a vault? Why do
// we have chats as part of memory? I don't understand what these are or the
// separation of them." There was nothing to understand. They were never one
// thing.
//
// So each went to the place that already owned it (D1), and none was deleted:
//
//   Chats  -> /chats, a first-class place with its own page and dock tile. The
//             per-chat sharing control went with it, onto the chat's own
//             toolbar (components/chat/ChatShare.tsx).
//   Vault  -> /apps/notes, the notes app's own page. It is that app's private
//             store and drawing it here was a scope violation as well as a
//             confusion.
//   Files  -> /chats/files (pages/ChatFiles.tsx). An attachment is a thing you
//             put in a chat.
//
// `?tab=` still resolves for all three, as a redirect, so every link handed out
// still lands somewhere correct.
//
// WHAT REMAINS IS ONE TREE AND THE DOCUMENT IT OPENS.
//
// 1. A NOTE IS A SCOPE AND AN ID, NOT A PATH. `?scope=&note=` and `?path=` are
//    still the addresses (services/refs.py hands both out), and nothing here
//    turned Memory into a filesystem. The tree is the kit's `FileTree` on a
//    desktop and its `FileDrill` on a phone, over ONE adapter
//    (./memory/MemoryTree.tsx).
//
// 2. THE ROOT SAYS WHAT ITS THREE KINDS ARE. Scopes, Roles and Documents are
//    three different address kinds that used to sit in one undifferentiated
//    list of roots, which is why `role:reviewer` and `Documents` read as
//    unexplained (D9). They are labelled groups now. Nothing moved on disk.
//
// 3. SEARCH AND New note LIVE ON THE TREE'S OWN TOOLBAR, in the page column,
//    built from kit controls. The page's own control row is gone: it drew a
//    second search, a second New and a Refresh above every open document (D5).
//
// 4. AN OPEN DOCUMENT GETS NO PAGE CHROME, on either viewport. That is
//    `directoryChrome` now, not a rule this page implements.
//
// 5. SEARCH IS A LIST. A query is not containment, so its results are a plain
//    list in the content pane and never a second tree.

// A helper for the `${r.status} ${await r.text()}` -> shortHttpError swap: a
// small, throwaway wrapper so every fetch call site below reads the same way
// rather than repeating the `.catch(() => "")` dance six times.
async function httpFail(r: Response): Promise<Error> {
  return new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}




type SemanticHit = {
  score: number;
  kind: string;
  ref: string;
  title: string | null;
  scope: string | null;
  ts: string | null;
  text: string;
  status: string;
};
type SemanticResult = {
  hits: SemanticHit[];
  mode: string;
  rebuilding: boolean;
  searched: number;
  corpus: number;
};

const OVERFLOW_AT = 100;



// ---- Page ------------------------------------------------------------------

/** Where the three retired tabs went (D1). `?tab=docs` is NOT here: it is a
 *  still-live deep link from services/refs.py's doc resolver, from before notes
 *  and documents merged, and it already means "open the Library", where its
 *  `?path=` selects the document. So it resolves by falling through. */
const MOVED: Record<string, string> = {
  chats: "/chats",
  vault: "/apps/notes",
  files: "/chats/files",
};

export default function Memory() {
  const [searchParams] = useSearchParams();
  const tab = searchParams.get("tab");
  const moved = tab ? MOVED[tab] : undefined;
  if (moved) {
    // `?file=` is the attachment viewer's deep link and is carried across, so a
    // link to ONE file still opens narrowed to that file. Every other parameter
    // was about a page that no longer exists here and is dropped.
    const file = searchParams.get("file");
    const to = moved === "/chats/files" && file
      ? `${moved}?file=${encodeURIComponent(file)}`
      : moved;
    return <Navigate to={to} replace />;
  }
  return <Library />;
}

// ---- Library: the tree, and the document it opens --------------------------

/** Rows a phone level draws before the rest becomes one line. The fourth list
 *  state: a scope with 4,000 notes reports itself instead of freezing the tab. */
const DRILL_MAX_ROWS = 100;

function Library() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const isWide = useIsWide();
  // ON A PHONE THIS PAGE IS ITS TREE (shell-mockup-spec-2026-08-22.md section
  // 4). The declaration drops the page's own header and the shell's breadcrumb,
  // because everything they would say is already on screen 40px above: bar one
  // says the place, the selected tab says the page. The rule and the reasoning
  // are in components/shell/pageChrome.ts.
  useDirectoryContent(!isWide);
  // Bumped by every write. A new source means a new adapter, which is how the
  // tree is told to drop what it had loaded and read the level again. It does
  // NOT reset which branches are open: `FileTree` clears its levels when the
  // adapter changes and leaves its open set alone, which is what makes a
  // reload safe to do behind the operator's back (see `staleReload` below).
  const [generation, setGeneration] = useState(0);
  const source = useMemo(() => createMemorySource(), [generation]);
  const [filterText, setFilterText] = useState("");
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

  // A TAB THAT HAS BEEN AWAY COMES BACK CURRENT (D5). Agents write notes while
  // this page sits open, so the tree does go stale; the operator should not have
  // to know that, or press anything for it. Only after 30 seconds hidden, so
  // flicking to another window and back does not reload under the cursor, and
  // only the LEVELS reload: `FileTree` keeps its open set across an adapter
  // change, so nothing the operator opened closes behind them.
  const hiddenAt = useRef<number | null>(null);
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) { hiddenAt.current = Date.now(); return; }
      const away = hiddenAt.current;
      hiddenAt.current = null;
      if (away !== null && Date.now() - away > 30_000) setGeneration((n) => n + 1);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const onChanged = useCallback((msg?: string) => {
    setNotice(msg ?? null);
    setGeneration((n) => n + 1);
  }, []);

  const onDeleted = useCallback(() => {
    clearSelection();
    setNotice("Note deleted.");
    setGeneration((n) => n + 1);
  }, [clearSelection]);

  // A label-based client-side filter for the desktop tree. Wrapping the adapter
  // creates a new object, which makes FileTree drop its cache and reload from
  // the filter-narrowed children on the next open.
  const filteredSource = useMemo(() => {
    if (!filterText) return source;
    const lower = filterText.toLowerCase();
    return {
      ...source,
      adapter: {
        ...source.adapter,
        children: async (node: Parameters<typeof source.adapter.children>[0]) => {
          const result = await source.adapter.children(node);
          return {
            ...result,
            nodes: result.nodes.filter((n) => n.label.toLowerCase().includes(lower)),
          };
        },
      },
    };
  }, [source, filterText]);

  // The desktop tree lives in column two. The page declares itself a directory
  // and portals its own tree into the slot the shell provides, so the shell
  // knows nothing about scopes or notes. Off on a phone, where the tree is this
  // page's content instead and mounting it twice would cost every request twice.
  const { host, open: treeOpen, setOpen } = useDirectoryPage({
    pageId: "memory",
    label: "Memory",
    enabled: isWide,
    // ARRIVING AT A NOTE DOES NOT OPEN THE PICKER OVER IT (D3). Following a
    // link put the tree over the page column on top of the document the link
    // had just opened, which the operator read, correctly, as the page not
    // knowing where it was. Read once, at registration; see `useDirectoryPage`.
    openOnArrival: !selected,
  });

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

  // AN OPEN DOCUMENT IS A FULL CONTENT TAKEOVER ON A PHONE
  // (shell-mockup-spec-2026-08-22.md section 6): the page toolbar collapses to
  // zero, the Up bar is gone because the document's own header carries the way
  // back, and this page's search row goes with them. It applies to a DOCUMENT
  // and not to a note, which keeps the record shape and its own Up control
  // (see MemoryDocument.tsx for why the two differ).
  const docTakeover = !isWide && !applied && selected?.kind === "doc";
  useContentTakeover(!!docTakeover);

  // What this view draws around the tree. `documentOpen` is "the pane is a
  // RECORD", which a bare `?scope=` is not: selecting a scope still leaves the
  // tree on screen, so the bottom bar stays with it.
  const chrome = directoryChrome({
    wide: isWide,
    documentOpen: !!applied || selected?.kind === "note" || selected?.kind === "doc",
  });
  const goUp = useCallback(() => {
    if (upTrail) setTrail(upTrail);
    clearSelection();
  }, [upTrail, clearSelection]);

  let pane: JSX.Element;
  if (applied) {
    pane = (
      <SearchResults
        query={applied}
        onSelect={select}
        onClear={() => { setFilterText(""); setParams((next) => next.delete("q")); }}
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
        onBack={!isWide ? goUp : undefined}
        backLabel={upTrail ? drillCursor(upTrail).label : undefined}
        takeover={docTakeover}
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
        // Arriving here opens the tree in column two, so the control that asks
        // for it is drawn only when it is not already on screen beside this.
        hint={treeOpen
          ? "Pick a note or a document in the tree, or write a new note."
          : "Open the tree to pick a note or a document, or write a new note."}
        action={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            {!treeOpen && (
              <Button size="sm" onClick={() => setOpen(true)}><FolderTree aria-hidden /> Open the tree</Button>
            )}
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}><Plus aria-hidden /> New note</Button>
          </Row>
        }
      />
    );
  }

  return (
    <div style={chrome.actionBar ? { paddingBottom: "var(--ws-pagebar-h)" } : undefined}>
      {host && createPortal(
        <>
          {/* THE TREE'S OWN TOOLBAR, and the only controls this page has (D5).
              It used to be a hand-built `<button>` and a bare `<input>` with
              their own CSS: a 34px bar holding 28px controls, the button with
              horizontal padding and no vertical centring so its icon sat on the
              text baseline, and the input with no border so the two did not
              read as the same kind of thing. The operator listed every one of
              those. They are kit controls now, so they are centred and sized
              because everything else in the system is.

              ONE FIELD DOES BOTH JOBS, because they were always one job under
              two names ("filter is just search under a different name"):
              typing narrows the TREE by label, instantly and locally; Enter
              runs the semantic search over everything and puts the results in
              the content pane. */}
          <div className="ws-pagecol__dirtoolbar">
            <form
              className="ws-dirtoolbar__search"
              onSubmit={(e) => {
                e.preventDefault();
                setNotice(null);
                setParams((next) => {
                  if (filterText.trim()) next.set("q", filterText.trim());
                  else next.delete("q");
                });
              }}
            >
              <Search size={13} aria-hidden className="ws-dirtoolbar__searchicon" />
              <Input
                className="ws-dirtoolbar__searchinput"
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="Search"
                aria-label="Search memory. Type to narrow the tree, press Enter to search everything."
              />
            </form>
            <Button
              size="sm"
              icon
              variant="ghost"
              onClick={() => { setNotice(null); setCreating(true); }}
              aria-label="New note"
              title="New note"
            >
              <FilePlus aria-hidden />
            </Button>
            {/* RELOAD, DEMOTED RATHER THAN DELETED. The operator asked why it
                exists at all ("why would the information be stale?"): agents
                write notes while this page is open and the tree caches
                whole-index responses, so it genuinely goes stale. What was
                wrong was its rank -- a labelled button in the content area,
                beside Search and New note, as if reloading were a thing the
                operator should be thinking about. It is an icon on the tree's
                own bar, and `staleReload` below means most of the time it is
                already done by the time he looks. */}
            <Button
              size="sm"
              icon
              variant="ghost"
              onClick={() => setGeneration((n) => n + 1)}
              aria-label="Reload memory"
              title="Reload"
            >
              <RefreshCw aria-hidden />
            </Button>
          </div>
          <div className="ws-pagecol__treescroll">
            <MemoryTreeView source={filteredSource} selected={selected} onSelect={select} />
          </div>
        </>,
        host,
      )}
      {/* THE PAGE HAS NO CONTROL ROW ANY MORE (D5). It used to draw Search,
          Refresh and New note here, above the tree on a desktop -- and, because
          `directoryChrome` consulted `documentOpen` only on a phone, above every
          open note and every open document too. All three live on the tree's own
          toolbar in the page column now, which is where a control that acts on
          the tree belongs, and an open document gets no page chrome on either
          viewport. */}
      {notice && !docTakeover && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
      )}
      {pane}
      {/* THE PHONE'S CONTROLS, AT THE THUMB. A phone has no page column, so it
          has no directory toolbar; this bar is its equivalent and carries the
          same three things in the same order. It goes when a document has the
          screen (shell-mockup-spec-2026-08-22.md section 6): the document's own
          header carries Save, and a bar over the last line of a note is not a
          bar the reader wants.

          SEARCH IS DRAWN HERE NOW. It used to be absent on a phone, deferred on
          the grounds that a dead control is worse than an absent one -- but the
          search backend it was waiting for exists (`GET /api/memory/search`),
          and the reason it could not simply be added, that a phone was already
          spending ~600px on chrome before its first row, went with the header,
          the description and the Segmented. */}
      {chrome.actionBar && (
        <PageActionBar>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setNotice(null);
              setParams((next) => {
                if (filterText.trim()) next.set("q", filterText.trim());
                else next.delete("q");
              });
            }}
            style={{ display: "flex", flex: "1 1 auto", minWidth: 0 }}
          >
            <Input
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Search"
              aria-label="Search memory. Type to narrow the list, press Enter to search everything."
              style={{ width: "100%" }}
            />
          </form>
          <Button
            icon
            aria-label="Reload memory"
            onClick={() => setGeneration((n) => n + 1)}
            style={{ flex: "0 0 auto" }}
          >
            <RefreshCw aria-hidden />
          </Button>
          <Button variant="primary" onClick={() => { setNotice(null); setCreating(true); }}>
            <FilePlus aria-hidden /> New note
          </Button>
        </PageActionBar>
      )}
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
// A query is not containment, so this is a list and never a tree. Results come
// from GET /api/memory/search, which calls semantic_index.search with no
// from_scope (the operator owns every scope). The old path called knowledge.search
// as an id filter with limit=1000 and ranked by directory order; there is no
// ranking to preserve there, so this is a full replacement.

const KIND_ICON: Record<string, typeof FileText> = {
  note: FileText,
  doc: FileText,
  chat: MessageSquare,
  ticket: CheckSquare,
};

function hitNoteId(ref: string): string {
  const name = ref.split("/").pop() ?? ref;
  return name.replace(/\.md$/, "");
}

function SearchResults({
  query,
  onSelect,
  onClear,
}: {
  query: string;
  onSelect: (node: MemoryNode) => void;
  onClear: () => void;
}) {
  const navigate = useNavigate();
  const [result, setResult] = useState<SemanticResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setResult(null);
    setErr(null);
    fetch(`/api/memory/search?q=${encodeURIComponent(query)}&limit=20`)
      .then(async (r) => {
        if (!r.ok) throw await httpFail(r);
        return r.json() as Promise<SemanticResult>;
      })
      .then((j) => { if (live) setResult(j); })
      .catch((e: unknown) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [query, attempt]);

  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't search Memory"
        hint={<><code>/api/memory/search</code> ({err})</>}
        action={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
      />
    );
  }
  if (!result) {
    return (
      <Stack gap="sm">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} width="70%" />)}
      </Stack>
    );
  }

  const { hits, corpus } = result;
  if (hits.length === 0) {
    return (
      <StateBlock
        title="Nothing matches"
        hint={<>No results for <strong>{query}</strong> in {corpus} indexed chunks. Clear the search to browse the tree.</>}
        action={<Button size="sm" onClick={onClear}>Clear search</Button>}
      />
    );
  }

  const shown = hits.slice(0, OVERFLOW_AT);
  const hidden = hits.length - shown.length;

  return (
    <Stack gap="lg">
      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
          {hits.length} {hits.length === 1 ? "result" : "results"} for {query}
          {result.rebuilding && " (index rebuilding)"}
        </span>
        <Button size="sm" onClick={onClear}>Clear search</Button>
      </Row>
      <div className="ws-memory__level" role="list">
        {shown.map((h, i) => {
          const Icon = KIND_ICON[h.kind] ?? FileText;
          const excerpt = h.text.length > 140 ? h.text.slice(0, 140).trimEnd() + "…" : h.text;
          if (h.kind === "note" && h.scope) {
            return (
              <button
                key={i}
                type="button"
                role="listitem"
                className="ws-memory__row"
                onClick={() => onSelect({ kind: "note", scope: h.scope!, id: hitNoteId(h.ref) })}
              >
                <Icon size={16} strokeWidth={1.8} aria-hidden />
                <span className="ws-memory__rowlabel">
                  {h.title || hitNoteId(h.ref)}
                  {h.status === "superseded" && <span className="ws-muted"> (superseded)</span>}
                </span>
                <span className="ws-memory__more ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  <Tag hue={catFor(h.scope!)}>{scopeLabel(h.scope!)}</Tag>
                  {" "}{h.score.toFixed(2)}
                </span>
                <span className="ws-muted ws-memory__excerpt" style={{ fontSize: "var(--fs-xs)" }}>
                  {excerpt}
                </span>
              </button>
            );
          }
          if (h.kind === "doc") {
            return (
              <button
                key={i}
                type="button"
                role="listitem"
                className="ws-memory__row"
                onClick={() => onSelect({ kind: "doc", path: h.ref })}
              >
                <Icon size={16} strokeWidth={1.8} aria-hidden />
                <span className="ws-memory__rowlabel">
                  {h.title || h.ref}
                  {h.status === "superseded" && <span className="ws-muted"> (superseded)</span>}
                </span>
                <span className="ws-memory__more ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {h.score.toFixed(2)}
                </span>
                <span className="ws-muted ws-memory__excerpt" style={{ fontSize: "var(--fs-xs)" }}>
                  {excerpt}
                </span>
              </button>
            );
          }
          if (h.kind === "chat" && h.scope) {
            return (
              <button
                key={i}
                type="button"
                role="listitem"
                className="ws-memory__row"
                onClick={() => navigate(`/chat/${encodeURIComponent(h.scope!)}?thread=${encodeURIComponent(h.ref)}`)}
              >
                <Icon size={16} strokeWidth={1.8} aria-hidden />
                <span className="ws-memory__rowlabel">{h.title || h.ref}</span>
                <span className="ws-memory__more ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  <Tag hue={catFor(h.scope!)}>{scopeLabel(h.scope!)}</Tag>
                  {" "}{h.score.toFixed(2)}
                </span>
                <span className="ws-muted ws-memory__excerpt" style={{ fontSize: "var(--fs-xs)" }}>
                  {excerpt}
                </span>
              </button>
            );
          }
          if (h.kind === "ticket") {
            return (
              <button
                key={i}
                type="button"
                role="listitem"
                className="ws-memory__row"
                onClick={() => navigate(`/tickets/${encodeURIComponent(h.scope || "workshop")}/${encodeURIComponent(h.ref)}`)}
              >
                <Icon size={16} strokeWidth={1.8} aria-hidden />
                <span className="ws-memory__rowlabel">{h.title || h.ref}</span>
                <span className="ws-memory__more ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {h.scope && <Tag hue={catFor(h.scope)}>{scopeLabel(h.scope)}</Tag>}
                  {" "}{h.score.toFixed(2)}
                </span>
                <span className="ws-muted ws-memory__excerpt" style={{ fontSize: "var(--fs-xs)" }}>
                  {excerpt}
                </span>
              </button>
            );
          }
          return (
            <div key={i} role="listitem" className="ws-memory__row">
              <Icon size={16} strokeWidth={1.8} aria-hidden />
              <span className="ws-memory__rowlabel">{h.title || h.ref}</span>
              <span className="ws-memory__more ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{h.score.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
      {hidden > 0 && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
          Showing the first {OVERFLOW_AT} of {hits.length}. Narrow the search to see the rest.
        </p>
      )}
    </Stack>
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
