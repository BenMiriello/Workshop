import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  RefreshCw, Plus, Pencil, Trash2, FileText, BookOpen, MessageSquare, Pin,
  ShieldCheck,
} from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  SectionLabel,
  Row,
  Stack,
  Segmented,
  Select,
  Input,
  Textarea,
  Field,
  Modal,
  ConfirmDialog,
  StateBlock,
  Notice,
  Skeleton,
  StatusDot,
  Table,
  TableScroll,
  Tag,
  Checkbox,
  catFor,
  type SegmentedOption,
} from "@workshop/ui";
import { scopeLabel, scopeHref } from "../lib/tasks";
import { orderScopes } from "../lib/devMode";
import AttachmentViewer from "../components/chat/AttachmentViewer";
import { Ref } from "../components/Ref";
import { backlinksFor } from "../lib/refs";
import { useResolvedRefs } from "../lib/useResolvedRefs";
import { shortHttpError } from "../lib/httpError";
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

// The Memory surface (memory-and-documentation-model-2026-08-14.md sec 11):
// one page over three substrates that used to be scattered -- the scoped
// knowledge store (Notes), the committed docs/ tree (Documents), and chat
// threads (Chats) -- unified into one filterable list, plus the Vault
// (a frozen Apple Notes export, kept separate per sec 9) and Files (chat
// attachments) as their own tabs.
//
// Same Segmented/filter/list/drawer language as Work. Tokens not hex, lucide
// not emoji, per frontend/CLAUDE.md. Four list states, Edit-mode for writes,
// ConfirmDialog for delete.

// A helper for the `${r.status} ${await r.text()}` -> shortHttpError swap: a
// small, throwaway wrapper so every fetch call site below reads the same way
// rather than repeating the `.catch(() => "")` dance six times.
async function httpFail(r: Response): Promise<Error> {
  return new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

type Note = {
  id: string;
  title: string;
  hook: string;
  updated?: string | null;
  scope: string;
  // Provenance and the ranked-digest facets (design doc sec 5, sec 11):
  // who wrote it, its type (decision/fact/reference/preference), and whether
  // it is pinned. Absent on a note written before this metadata existed --
  // `author` renders as "Unknown author" and `type`/`pinned` default like the
  // backend does (fact / not pinned).
  author?: string;
  type?: string;
  pinned?: boolean;
};

type NotesResp = { notes: Note[]; count: number; scopes?: number };

// A doc from the docs/ index (GET /api/documents). Committed markdown; edits
// land in the working tree (PUT /api/documents/file) and are NOT auto-
// committed -- the operator commits them on their own schedule, same as any
// other tracked-file edit.
type Doc = {
  path: string;
  title: string;
  section: string;
  mtime?: string | null;
};
type DocsResp = { docs: Doc[]; count: number };

// A thread summary (GET /api/chat/threads). Chats are the third source in
// the unified list -- shown, not edited here; a row navigates to the chat.
type ChatSummary = {
  id: string;
  title: string;
  context_key: string;
  scope?: string;
  created_at?: string | null;
  last_user_at?: string | null;
  archived_at?: string | null;
};

// One entry in the scope filter (GET /api/notes/scopes/tree). `parent` is the
// containing scope, or null at a root; `depth` is how far under a visible
// root this scope sits. Root-first, depth-first order from the backend, so
// rendering it as a flat, indented list already reads as a tree.
type ScopeNode = { key: string; parent: string | null; depth: number };

// One note in the ~/notes vault, served through the Store door
// (POST /api/stores/notes:vault/read). The vault is a plain folder of Markdown a
// real Obsidian opens; the Store URI is what makes it addressable by a grant.
type VaultNote = { path: string; title: string; size?: number; modified?: number };
// What the door returns. `tier` is "operator" for us — the operator is served
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

const OVERFLOW_AT = 100;

// Vault and Files stay their own tabs (sec 9: the vault is a dead one-time
// import and explicitly out of the unified list). Notes, Documents and Chats
// merge into "library", one list with a source badge per row and a filter
// over it (sec 11).
type Tab = "library" | "vault" | "files";
const TABS: SegmentedOption<Tab>[] = [
  { value: "library", label: "Library" },
  { value: "vault", label: "Vault" },
  { value: "files", label: "Files" },
];

type Source = "note" | "doc" | "chat";
type SourceFilter = "all" | Source;
const SOURCE_OPTIONS: SegmentedOption<SourceFilter>[] = [
  { value: "all", label: "All" },
  { value: "note", label: "Notes" },
  { value: "doc", label: "Documents" },
  { value: "chat", label: "Chats" },
];
const SOURCE_ICON: Record<Source, typeof FileText> = {
  note: FileText,
  doc: BookOpen,
  chat: MessageSquare,
};
const SOURCE_LABEL: Record<Source, string> = { note: "Note", doc: "Document", chat: "Chat" };

function SourceBadge({ source }: { source: Source }) {
  const Icon = SOURCE_ICON[source];
  return (
    <Tag hue={catFor(source)}>
      <Icon size={12} strokeWidth={1.8} aria-hidden /> {SOURCE_LABEL[source]}
    </Tag>
  );
}

const NOTE_TYPES = ["decision", "fact", "reference", "preference"] as const;
type NoteType = (typeof NOTE_TYPES)[number];
function noteTypeLabel(t?: string): string {
  const v = (t || "fact").trim().toLowerCase();
  return v.charAt(0).toUpperCase() + v.slice(1);
}

// A file the operator attached to a chat (GET /api/chat/attachments).
//
// Files live HERE rather than on a page of their own because this page is
// already "what this system knows, by scope": notes are the text half and an
// attached image or PDF is the binary half. Same scope vocabulary, same filter,
// same four list states — so the tab is a read of data the attachment index
// already carries rather than a second knowledge surface with its own rules.
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
};
type FilesResp = { files: StoredFile[]; count: number; scopes: string[] };

function fmtTs(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Who wrote it (design doc sec 5, sec 11): `author` is an origin string
// (services/origin on the backend) -- "operator", "self", "agent:<slug>",
// "derived:<model>", "tool:<name>", or "" on a note written before this
// metadata existed.
function authorLabel(author?: string): string {
  const a = (author || "").trim();
  if (!a) return "Unknown author";
  if (a === "operator") return "You";
  if (a === "self") return "The assistant";
  if (a.startsWith("agent:")) return `Agent: ${a.slice(6)}`;
  if (a.startsWith("derived:")) return `Distilled by ${a.slice(8)}`;
  if (a.startsWith("tool:")) return `Tool: ${a.slice(5)}`;
  return a;
}

// Any `workshop:chat/...` refs named in a note's body -- the note's OWN
// provenance, not "referenced by" (see ReferencedBy below, which is the
// reverse direction). Refs are written into note bodies by agents that file a
// note from a chat (design doc sec 10); this renders whichever ones are
// there as navigable chips instead of leaving them as inert text.
const CHAT_REF_RE = /workshop:chat\/[^\s)\]"'<>]+/g;
function chatRefsIn(body: string): string[] {
  const found = body.match(CHAT_REF_RE) ?? [];
  return Array.from(new Set(found));
}

// A note id must match the store's slug rule ([a-z0-9] segments joined by "-").
// Derive one from the title, fall back to "note", and always append a base36
// timestamp so a fresh note never collides with an existing file.
function freshNoteId(title: string): string {
  const base =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "note";
  return `${base}-${Date.now().toString(36)}`;
}

// Every scope `root`'s descendants, per the tree endpoint -- the frontend half
// of "everything below it" (sec 4.2's `reach`, sec 11's scope filter). Notes
// are already filtered this way server-side (`GET /api/notes?scope=` calls
// `scope_tree.reach`); chats have no such endpoint, so the same containment
// is recomputed here from the same tree data and applied client-side.
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
  // resolver hands it out) from before Notes and Documents merged into one
  // Library tab; it now just means "open Library with the Documents filter".
  const urlTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    urlTab === "vault" || urlTab === "files" ? (urlTab as Tab) : "library",
  );
  const initialSource: SourceFilter = urlTab === "docs" ? "doc" : "all";

  return (
    <div>
      <PageHeader
        title="Memory"
        sub="Notes, documents and chats in one place, filterable by scope. What agents read and write."
      />
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Memory view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "vault" ? (
        <VaultNotes />
      ) : tab === "files" ? (
        <Files />
      ) : (
        <Library initialSource={initialSource} />
      )}
    </div>
  );
}

// ---- Library (Notes + Documents + Chats, unified) --------------------------

// One chat's entry in the sharing model (sec 6). It sits OUTSIDE the card's
// `Link`, because a select nested inside a link is not operable.
//
// CURATION, NOT SECURITY. This changes what other chats' searches turn up. It
// does not restrict what anything can read: everything here runs as one uid.
// Per SEC-A2, do not relabel this permission or access.
//
// Explicit Save rather than commit-on-change, per frontend/CLAUDE.md rule 7:
// this writes config. The button appears only once the value actually differs,
// so a row the operator is only reading stays quiet.
function ChatShare({
  threadId, scope, policy, onSaved,
}: {
  threadId: string;
  scope: string;
  policy: MemoryPolicy | null;
  onSaved: (p: MemoryPolicy) => void;
}) {
  const ref = chatItemRef(threadId);
  const stored = effectiveShare(policy, "chat", scope, ref);
  const [value, setValue] = useState<ShareValue>(stored);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Follow the stored value when the policy loads or another row changes it,
  // but never stomp an edit in progress.
  useEffect(() => { setValue(stored); }, [stored]);

  const dirty = value !== stored;
  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      onSaved(await setItemShare(ref, value));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [ref, value, onSaved]);

  return (
    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center", marginTop: "var(--space-2)" }}>
      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Other chats find it:</span>
      <Select
        value={value}
        onChange={(e) => setValue(e.target.value as ShareValue)}
        aria-label={`What other chats find from this chat`}
      >
        {SHARE_VALUES.map((v) => (
          <option key={v} value={v}>{SHARE_LABEL[v]}</option>
        ))}
      </Select>
      {dirty && (
        <Button variant="primary" onClick={() => { void save(); }} loading={saving}>Save</Button>
      )}
      {err ? (
        <span className="ws-field__error">{err}</span>
      ) : (
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{SHARE_HELP[value]}</span>
      )}
    </Row>
  );
}

function Library({ initialSource }: { initialSource: SourceFilter }) {
  const [searchParams] = useSearchParams();
  const [notesData, setNotesData] = useState<NotesResp | null>(null);
  const [docsData, setDocsData] = useState<DocsResp | null>(null);
  const [chatsData, setChatsData] = useState<ChatSummary[] | null>(null);
  // What other chats find when they search (sec 6). Loaded once; a failure here
  // must never take the list down, so it stays null and the controls read as
  // the shipped defaults.
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  const [scopeTree, setScopeTree] = useState<ScopeNode[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Honor a ?scope= deep link (a resolved note ref, or "View all in Memory"
  // links from Project and Agent detail).
  const [scope, setScope] = useState<string>(searchParams.get("scope") || "all");
  const [source, setSource] = useState<SourceFilter>(initialSource);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [open, setOpen] = useState<
    | { mode: "create" }
    | { mode: "editNote"; note: Note }
    | { mode: "editDoc"; doc: Doc }
    | null
  >(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (scope !== "all") params.set("scope", scope);
      if (applied.trim()) params.set("q", applied.trim());
      const qs = params.toString();
      const [notesR, docsR, chatsR] = await Promise.all([
        fetch(`/api/notes${qs ? `?${qs}` : ""}`),
        fetch("/api/documents"),
        fetch("/api/chat/threads"),
      ]);
      if (!notesR.ok) throw await httpFail(notesR);
      if (!docsR.ok) throw await httpFail(docsR);
      if (!chatsR.ok) throw await httpFail(chatsR);
      setNotesData(await notesR.json());
      setDocsData(await docsR.json());
      setChatsData(await chatsR.json());
      // Best effort and deliberately not awaited into the failure path above:
      // the library is still readable without it.
      void getPolicy().then(setPolicy).catch(() => {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, applied]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    fetch("/api/notes/scopes/tree")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopeTree(j.scopes ?? []))
      .catch(() => setScopeTree([]));
  }, []);

  const scopeOptions = useMemo(
    () => orderScopes(scopeTree.map((n) => n.key)).map((key) => {
      const node = scopeTree.find((n) => n.key === key);
      const depth = node?.depth ?? 0;
      return { key, label: "  ".repeat(depth) + scopeLabel(key) };
    }),
    [scopeTree],
  );

  const reachSet = useMemo(
    () => (scope === "all" ? null : reachSetFrom(scopeTree, scope)),
    [scope, scopeTree],
  );

  const notes = notesData?.notes ?? [];

  // Documents have no scope of their own (docs/ is a single committed tree),
  // so the scope filter does not apply to them -- only the source filter and
  // the search do. Chats DO have a scope, filtered client-side against the
  // same reach set the notes endpoint already applied server-side.
  const q = applied.trim().toLowerCase();
  const docs = (docsData?.docs ?? []).filter(
    (d) => !q || d.title.toLowerCase().includes(q) || d.path.toLowerCase().includes(q),
  );
  const chats = (chatsData ?? []).filter((c) => !c.archived_at)
    .filter((c) => !q || c.title.toLowerCase().includes(q))
    .filter((c) => !reachSet || reachSet.has(c.scope || "workshop"));

  type Item = {
    key: string;
    source: Source;
    sortKey: string;
    note?: Note;
    doc?: Doc;
    chat?: ChatSummary;
  };
  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    if (source === "all" || source === "note") {
      for (const n of notes) out.push({ key: `note:${n.scope}:${n.id}`, source: "note", sortKey: n.updated || "", note: n });
    }
    if (source === "all" || source === "doc") {
      for (const d of docs) out.push({ key: `doc:${d.path}`, source: "doc", sortKey: d.mtime || "", doc: d });
    }
    if (source === "all" || source === "chat") {
      for (const c of chats) out.push({ key: `chat:${c.id}`, source: "chat", sortKey: c.last_user_at || c.created_at || "", chat: c });
    }
    out.sort((a, b) => (a.sortKey < b.sortKey ? 1 : a.sortKey > b.sortKey ? -1 : 0));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notesData, docsData, chatsData, source, applied, reachSet]);

  // Open the note/doc a deep link names, once. A chat receipt or a `<Ref>`
  // chip links here ("Saved a note to workshop", a doc chip in a note body),
  // and landing on a filtered list still leaves the operator hunting for the
  // one item they clicked. Fires once per id: without the guard, closing the
  // modal would immediately reopen it, since the param is still in the URL.
  const wantNote = searchParams.get("note");
  const wantPath = searchParams.get("path");
  const openedRef = useRef<string | null>(null);
  useEffect(() => {
    if (wantNote && openedRef.current !== `note:${wantNote}` && notes.length) {
      const hit = notes.find((n) => n.id === wantNote);
      if (hit) {
        openedRef.current = `note:${wantNote}`;
        setOpen({ mode: "editNote", note: hit });
      }
    } else if (wantPath && openedRef.current !== `doc:${wantPath}` && docs.length) {
      const hit = docs.find((d) => d.path === wantPath);
      if (hit) {
        openedRef.current = `doc:${wantPath}`;
        setOpen({ mode: "editDoc", doc: hit });
      }
    }
  }, [wantNote, wantPath, notes, docs]);

  const overflow = items.length > OVERFLOW_AT;
  const shown = overflow ? items.slice(0, OVERFLOW_AT) : items;

  const onChanged = useCallback(
    (msg?: string) => {
      setNotice(msg ?? null);
      refresh();
    },
    [refresh],
  );

  const submitSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setApplied(query);
    },
    [query],
  );

  const filtered = scope !== "all" || source !== "all" || applied.trim() !== "";

  const filterBar = (
    <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
      <Field label="Scope" inline>
        <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Filter by scope">
          <option value="all">All scopes</option>
          {scopeOptions.map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </Select>
      </Field>
      <Field label="Source" inline>
        <Segmented<SourceFilter> ariaLabel="Filter by source" value={source} onChange={setSource} options={SOURCE_OPTIONS} />
      </Field>
      <Field label="Search" inline>
        <form onSubmit={submitSearch} style={{ display: "flex", gap: "var(--space-2)" }}>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find in title or body"
            aria-label="Search"
          />
          <Button size="sm" type="submit">Search</Button>
        </form>
      </Field>
      <Button size="sm" onClick={refresh} loading={loading}>
        <RefreshCw aria-hidden /> Refresh
      </Button>
      <Button size="sm" variant="primary" onClick={() => { setNotice(null); setOpen({ mode: "create" }); }}>
        <Plus aria-hidden /> New note
      </Button>
    </Row>
  );

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load Memory"
        hint={err}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!notesData || !docsData || !chatsData) {
    body = (
      <CardGrid>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <Stack gap="sm">
              <Skeleton width="60%" />
              <Skeleton width="80%" />
              <Skeleton width="30%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (items.length === 0) {
    body = (
      <StateBlock
        title={filtered ? "Nothing matches these filters" : "Nothing here yet"}
        hint={
          filtered ? (
            <>Nothing matches. Reset <strong>Scope</strong> and <strong>Source</strong> to <strong>All</strong>, or clear the search.</>
          ) : (
            <>Notes are shared with your agents. Create one with <strong>New note</strong>, or an agent will leave one as it works.</>
          )
        }
        action={<Button size="sm" variant="primary" onClick={() => setOpen({ mode: "create" })}><Plus aria-hidden /> New note</Button>}
      />
    );
  } else {
    body = (
      <>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>
            Showing the first {OVERFLOW_AT} of {items.length}. Narrow by scope, source or search to see the rest.
          </p>
        )}
        <CardGrid className="ws-notes-grid">
          {shown.map((it) => (
            <li key={it.key}>
              {it.source === "note" && it.note ? (
                <Card
                  interactive
                  compact
                  onClick={() => { setNotice(null); setOpen({ mode: "editNote", note: it.note! }); }}
                  style={{ cursor: "pointer" }}
                  className="ws-note-card"
                >
                  <Stack gap="sm">
                    <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <SourceBadge source="note" />
                      {it.note.pinned && <Pin size={13} strokeWidth={1.8} aria-hidden />}
                    </Row>
                    <span className="ws-note-card__title">{it.note.title || it.note.id}</span>
                    <span className="ws-note-card__hook">{it.note.hook || ""}</span>
                    <Row wrap className="ws-note-card__footer" style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <Tag hue={catFor(it.note.scope || "workshop")}>{scopeLabel(it.note.scope)}</Tag>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{noteTypeLabel(it.note.type)}</span>
                      {it.note.updated && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(it.note.updated)}</span>
                      )}
                    </Row>
                  </Stack>
                </Card>
              ) : it.source === "doc" && it.doc ? (
                <Card
                  interactive
                  compact
                  onClick={() => { setNotice(null); setOpen({ mode: "editDoc", doc: it.doc! }); }}
                  style={{ cursor: "pointer" }}
                  className="ws-doc-card"
                >
                  <Stack gap="sm">
                    <SourceBadge source="doc" />
                    <span className="ws-note-card__title">{it.doc.title}</span>
                    <span className="ws-doc-card__path">{it.doc.path}</span>
                    {it.doc.mtime && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(it.doc.mtime)}</span>
                    )}
                  </Stack>
                </Card>
              ) : it.chat ? (
                <Link to={`/chat/${encodeURIComponent(it.chat.context_key)}?thread=${encodeURIComponent(it.chat.id)}`} className="ws-note-card__link">
                  <Card interactive compact className="ws-note-card">
                    <Stack gap="sm">
                      <SourceBadge source="chat" />
                      <span className="ws-note-card__title">{it.chat.title || "Chat"}</span>
                      <Row wrap className="ws-note-card__footer" style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <Tag hue={catFor(it.chat.scope || "workshop")}>{scopeLabel(it.chat.scope)}</Tag>
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                          {fmtTs(it.chat.last_user_at || it.chat.created_at)}
                        </span>
                      </Row>
                    </Stack>
                  </Card>
                </Link>
              ) : null}
              {it.chat && (
                <ChatShare
                  threadId={it.chat.id}
                  scope={it.chat.scope || "workshop"}
                  policy={policy}
                  onSaved={setPolicy}
                />
              )}
            </li>
          ))}
        </CardGrid>
      </>
    );
  }

  return (
    <div>
      {notice && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
      )}
      <div style={{ marginBottom: "var(--space-2)" }}>{filterBar}</div>
      {scope !== "all" ? (
        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginBottom: "var(--space-4)" }}>
          Showing {scopeLabel(scope)} and everything below it.
        </p>
      ) : (
        <div style={{ marginBottom: "var(--space-4)" }} />
      )}
      {body}
      {open?.mode === "create" && (
        <CreateNote
          scopes={scopeTree.map((n) => n.key)}
          initialScope={scope !== "all" ? scope : undefined}
          onClose={() => setOpen(null)}
          onCreated={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
      {open?.mode === "editNote" && (
        <EditNote
          note={open.note}
          onClose={() => setOpen(null)}
          onChanged={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
      {open?.mode === "editDoc" && (
        <EditDoc
          doc={open.doc}
          onClose={() => setOpen(null)}
          onChanged={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
    </div>
  );
}

// ---- Create drawer ---------------------------------------------------------

function CreateNote({
  scopes,
  initialScope,
  onClose,
  onCreated,
}: {
  scopes: string[];
  initialScope?: string;
  onClose: () => void;
  onCreated: (msg?: string) => void;
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
      onCreated(`Note saved to ${scopeLabel(scope)}.`);
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

// "Referenced by" on a note (design doc sec 10: "backlinks fall out"; sec 12
// Phase 3: "surface it at least on the note view"). An index read
// (GET /api/refs/backlinks), never a corpus scan, and the batch resolve call
// so N backlinks cost one request, not N. Renders nothing when there are none
// -- an inline "no backlinks" line would out-noise the body on every note
// written before this existed.
function ReferencedBy({ scope, noteId }: { scope: string; noteId: string }) {
  const target = `workshop:note/${scope}/${noteId}`;
  const [sources, setSources] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    backlinksFor(target)
      .then((refs) => { if (live) setSources(refs); })
      .catch(() => { if (live) setSources([]); });
    return () => { live = false; };
  }, [target]);
  const resolved = useResolvedRefs(sources ?? []);
  if (!sources || !sources.length) return null;
  return (
    <Stack gap="sm">
      <SectionLabel>Referenced by</SectionLabel>
      <Row wrap style={{ gap: "var(--space-2)" }}>
        {sources.map((r) => <Ref key={r} resolved={resolved[r]} />)}
      </Row>
    </Stack>
  );
}

// Provenance: author, timestamp, and the originating chat if the body names
// one. Never a raw path or a copyable URL (design doc sec 10's acceptance
// test) -- a chat ref renders as a `<Ref>` chip that navigates, exactly like
// ReferencedBy above.
function Provenance({ author, updated, body }: { author?: string; updated?: string | null; body: string }) {
  const chatRefs = useMemo(() => chatRefsIn(body), [body]);
  const resolved = useResolvedRefs(chatRefs);
  return (
    <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{authorLabel(author)}</span>
      {updated && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{fmtTs(updated)}</span>}
      {chatRefs.map((r) => <Ref key={r} resolved={resolved[r]} />)}
    </Row>
  );
}

// ---- Edit drawer -----------------------------------------------------------

type EditDraft = { title: string; hook: string; content: string; type: NoteType; pinned: boolean };

function EditNote({
  note,
  onClose,
  onChanged,
}: {
  note: Note;
  onClose: () => void;
  onChanged: (msg?: string) => void;
}) {
  const scope = note.scope;
  const idPath = `${encodeURIComponent(scope)}/notes/${encodeURIComponent(note.id)}`;
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ author: string; type: NoteType; pinned: boolean }>(
    { author: note.author || "", type: (note.type as NoteType) || "fact", pinned: !!note.pinned },
  );
  const [draft, setDraft] = useState<EditDraft>({
    title: note.title, hook: note.hook, content: "", type: "fact", pinned: false,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Load the note body plus its provenance (the list carries only
  // id/title/hook/updated/author/type/pinned -- the last three summary-level,
  // the body and the confirmed-fresh meta come from the per-note GET).
  useEffect(() => {
    let live = true;
    setLoading(true);
    fetch(`/api/knowledge/${idPath}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!live) return;
        const bodyText = j?.content ?? "";
        const m = {
          author: j?.author ?? note.author ?? "",
          type: (j?.type as NoteType) ?? (note.type as NoteType) ?? "fact",
          pinned: j?.pinned ?? !!note.pinned,
        };
        setContent(bodyText);
        setMeta(m);
        setDraft({ title: note.title, hook: note.hook, content: bodyText, type: m.type, pinned: m.pinned });
      })
      .catch(() => { if (live) setContent(""); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [idPath, note.title, note.hook, note.author, note.type, note.pinned]);

  const setField = <K extends keyof EditDraft>(k: K, v: EditDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const baseDraft: EditDraft = {
    title: note.title, hook: note.hook, content: content ?? "", type: meta.type, pinned: meta.pinned,
  };
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(baseDraft);

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch(`/api/knowledge/${idPath}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          title: draft.title.trim(), content: draft.content, hook: draft.hook.trim(),
          // `author` is deliberately omitted: an edit does not reassign who
          // wrote the note. `note_type`/`pinned` are sent explicitly, since
          // the operator just chose them.
          note_type: draft.type, pinned: draft.pinned,
        }),
      });
      if (!r.ok) throw await httpFail(r);
      setContent(draft.content);
      setMeta((m) => ({ ...m, type: draft.type, pinned: draft.pinned }));
      setEditing(false);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, idPath, onChanged]);

  const del = useCallback(async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/knowledge/${idPath}`, { method: "DELETE" });
      if (!r.ok) throw await httpFail(r);
      onChanged("Note deleted.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
      setConfirmDel(false);
    }
  }, [idPath, onChanged]);

  const href = scopeHref(scope);

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? "Edit note" : (note.title || note.id)}
      size="lg"
      actions={
        editing ? (
          <>
            <Button onClick={() => { setEditing(false); setDraft(baseDraft); }}>Cancel</Button>
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>Save</Button>
          </>
        ) : (
          <>
            <Button variant="danger-ghost" onClick={() => setConfirmDel(true)}>
              <Trash2 aria-hidden /> Delete
            </Button>
            <Button variant="primary" disabled={loading} onClick={() => { setDraft(baseDraft); setEditing(true); }}>
              <Pencil aria-hidden /> Edit
            </Button>
          </>
        )
      }
    >
      <Stack gap="lg">
        {editing ? (
          <>
            <Field label="Title" htmlFor="edit-note-title">
              <Input id="edit-note-title" value={draft.title} onChange={(e) => setField("title", e.target.value)} data-autofocus />
            </Field>
            <Field label="Hook" htmlFor="edit-note-hook" optional help="A one-line summary shown in the index and lists.">
              <Input id="edit-note-hook" value={draft.hook} onChange={(e) => setField("hook", e.target.value)} />
            </Field>
            <Row wrap style={{ gap: "var(--space-4)" }}>
              <Field label="Type" htmlFor="edit-note-type" inline>
                <Select id="edit-note-type" value={draft.type} onChange={(e) => setField("type", e.target.value as NoteType)}>
                  {NOTE_TYPES.map((t) => (
                    <option key={t} value={t}>{noteTypeLabel(t)}</option>
                  ))}
                </Select>
              </Field>
              <Checkbox checked={draft.pinned} onChange={(v) => setField("pinned", v)} label="Pinned" />
            </Row>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              Pinned notes, then preferences and decisions, rank first in what gets read into every chat in this scope.
            </p>
            <Field label="Body" htmlFor="edit-note-body" optional>
              <Textarea id="edit-note-body" rows={12} value={draft.content} onChange={(e) => setField("content", e.target.value)} />
            </Field>
            {err && <p className="ws-field__error">{err}</p>}
            {dirty && <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>Unsaved changes.</p>}
          </>
        ) : (
          <>
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
              {href ? (
                <Link to={href} className="ws-bc__link" onClick={onClose}>
                  <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
                </Link>
              ) : (
                <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
              )}
              <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{noteTypeLabel(meta.type)}</span>
              {meta.pinned && (
                <StatusDot status="ok"><Pin size={12} strokeWidth={1.8} aria-hidden /> Pinned</StatusDot>
              )}
            </Row>
            {note.hook && <p className="ws-muted" style={{ margin: 0 }}>{note.hook}</p>}
            {loading ? (
              <Skeleton width="70%" />
            ) : content && content.trim() ? (
              <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>{content}</p>
            ) : (
              <p className="ws-muted" style={{ margin: 0 }}>This note has no body yet.</p>
            )}
            {!loading && <Provenance author={meta.author} updated={note.updated} body={content ?? ""} />}
            {!loading && <ReferencedBy scope={scope} noteId={note.id} />}
            {flash && <p className="ws-muted" style={{ margin: 0 }}>Saved.</p>}
            {err && <p className="ws-field__error">{err}</p>}
          </>
        )}
      </Stack>

      <ConfirmDialog
        open={confirmDel}
        onCancel={() => setConfirmDel(false)}
        onConfirm={del}
        busy={saving}
        title="Delete this note?"
        body={<>This removes the note from <strong>{scopeLabel(scope)}</strong>. It can't be undone.</>}
        confirmLabel="Delete note"
      />
    </Modal>
  );
}

// ---- Document editor ---------------------------------------------------------
// Docs stay committed markdown under docs/ (git history and editor-openability
// are the point); this modal reads and writes the raw file through
// GET/PUT /api/documents/file, with the same path containment on both sides.
// A save lands in the working tree only -- there is no commit here, and the
// modal says so, so a save does not look lost.
function EditDoc({
  doc,
  onClose,
  onChanged,
}: {
  doc: Doc;
  onClose: () => void;
  onChanged: (msg?: string) => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setErr(null);
    fetch(`/api/documents/file?path=${encodeURIComponent(doc.path)}`)
      .then(async (r) => {
        if (!r.ok) throw await httpFail(r);
        return r.json();
      })
      .then((j) => { if (live) { setContent(j?.content ?? ""); setDraft(j?.content ?? ""); } })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [doc.path]);

  const dirty = editing && draft !== (content ?? "");

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch("/api/documents/file", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: doc.path, content: draft }),
      });
      if (!r.ok) throw await httpFail(r);
      setContent(draft);
      setEditing(false);
      onChanged("Saved. Not committed; commit it yourself when ready.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [doc.path, draft, onChanged]);

  return (
    <Modal
      open
      onClose={onClose}
      title={doc.title}
      size="lg"
      actions={
        editing ? (
          <>
            <Button onClick={() => { setEditing(false); setDraft(content ?? ""); }}>Cancel</Button>
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>Save</Button>
          </>
        ) : (
          <>
            <Button onClick={onClose}>Close</Button>
            <Button variant="primary" disabled={loading} onClick={() => setEditing(true)}>
              <Pencil aria-hidden /> Edit
            </Button>
          </>
        )
      }
    >
      <Stack gap="lg">
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", wordBreak: "break-all" }}>{doc.path}</span>
        {editing && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
            Saving edits the file on disk. It is not committed; commit it yourself when ready.
          </p>
        )}
        {loading ? (
          <Stack gap="sm">
            <Skeleton width="80%" />
            <Skeleton width="90%" />
            <Skeleton width="60%" />
          </Stack>
        ) : err ? (
          <Notice tone="error" where={<code>/api/documents/file</code>} detail={err}>Couldn't load this document.</Notice>
        ) : editing ? (
          <Textarea
            rows={20}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            data-autofocus
            style={{ fontFamily: "var(--font-mono, monospace)" }}
          />
        ) : (
          <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontSize: "var(--fs-sm)" }}>{content}</p>
        )}
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
                      : <Tag hue={catFor(f.kind)}>{f.kind}</Tag>}
                  </td>
                  <td>{fmtBytes(f.size)}</td>
                  <td>
                    {f.scope
                      ? <Tag hue={catFor(f.scope)}>{scopeLabel(f.scope)}</Tag>
                      : <span className="ws-muted">unscoped</span>}
                  </td>
                  <td>{fmtTs(f.created_at)}</td>
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
          file={{ id: viewing.id, name: viewing.name, kind: viewing.kind }}
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

// Whole numbers below a megabyte: the tenths of a KB are noise in a table.
function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
