import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw, Plus, Pencil, Trash2, FileText, ShieldCheck } from "lucide-react";
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
  Skeleton,
  StatusDot,
  Table,
  TableScroll,
  Tag,
  catFor,
  type SegmentedOption,
} from "@workshop/ui";
import { scopeLabel, scopeHref } from "../lib/tasks";
import { orderScopes } from "../lib/devMode";
import AttachmentViewer from "../components/chat/AttachmentViewer";
import { Ref } from "../components/Ref";
import { backlinksFor } from "../lib/refs";
import { useResolvedRefs } from "../lib/useResolvedRefs";

// The Notes surface (notes-system plan Phase N1): one /notes page over the
// EXISTING scoped knowledge store. A note IS a knowledge note; the per-note CRUD
// routes stay `/api/knowledge/{scope}/notes/{id}`. This page only adds the
// cross-scope list (GET /api/notes) + a scope filter + an editor drawer.
//
// Notes mirror Work: same Segmented/filter/list/drawer language, four list
// states, Edit-mode for writes, ConfirmDialog for delete. Tokens not hex, lucide
// not emoji, per frontend/CLAUDE.md.

type Note = {
  id: string;
  title: string;
  hook: string;
  updated?: string | null;
  scope: string;
};

type NotesResp = { notes: Note[]; count: number; scopes?: number };

// A doc from the read-only docs index (GET /api/documents). Notes are editable scoped
// knowledge; docs are committed markdown under docs/, surfaced read-only here so
// the operator sees notes and documents from one place (notes-system plan N3).
type Doc = {
  path: string;
  title: string;
  section: string;
  mtime?: string | null;
};
type DocsResp = { docs: Doc[]; count: number };

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

// Filter view: all scopes or one scope. The Segmented over scopes would grow
// unbounded (a scope per project + agent), so scope is a Select. The Segmented
// switches between the editable Notes list and the read-only Documents index.
type Tab = "notes" | "vault" | "docs" | "files";
const TABS: SegmentedOption<Tab>[] = [
  { value: "notes", label: "Notes" },
  { value: "vault", label: "Vault" },
  { value: "docs", label: "Documents" },
  { value: "files", label: "Files" },
];

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

// ---- Page ------------------------------------------------------------------

export default function Notes() {
  const [searchParams] = useSearchParams();
  // Honour ?tab=. The attachment viewer links here with ?tab=files, and without
  // this the link landed on Notes and the operator had to find the tab again.
  const urlTab = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(
    TABS.some((t) => t.value === urlTab) ? (urlTab as Tab) : "notes",
  );
  const [data, setData] = useState<NotesResp | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Honor a ?scope= deep link (the "View all in Notes" links from Project and
  // Agent detail pass the scope so the operator lands pre-filtered).
  const [scope, setScope] = useState<string>(searchParams.get("scope") || "all");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [open, setOpen] = useState<
    { mode: "create" } | { mode: "edit"; note: Note } | null
  >(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (scope !== "all") params.set("scope", scope);
      if (applied.trim()) params.set("q", applied.trim());
      const q = params.toString();
      const r = await fetch(`/api/notes${q ? `?${q}` : ""}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
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
    // `orderScopes` puts Development above Workshop and drops it entirely from
    // a non-dev bundle, so a bundle built without VITE_DEV_MODE never offers the
    // scope even when the API it is talking to is a developer install. Same call
    // Work.tsx makes on its own scope list. ADR 0014.
    fetch("/api/notes/scopes")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopes(orderScopes(j.scopes ?? [])))
      .catch(() => setScopes([]));
  }, []);

  const notes = data?.notes ?? [];

  // Open the note a `?note=` deep link names, once. A chat receipt links here
  // ("Saved a note to workshop" used to be a dead sentence), and landing on a
  // filtered grid still leaves the operator hunting for the one note they
  // clicked. Fires once per id: without the guard, closing the modal would
  // immediately reopen it, since the param is still in the URL.
  const wantNote = searchParams.get("note");
  const openedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!wantNote || openedRef.current === wantNote || !notes.length) return;
    const hit = notes.find((n) => n.id === wantNote);
    if (!hit) return;
    openedRef.current = wantNote;
    setOpen({ mode: "edit", note: hit });
  }, [wantNote, notes]);

  const overflow = notes.length > OVERFLOW_AT;
  const shown = overflow ? notes.slice(0, OVERFLOW_AT) : notes;

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

  const filterBar = (
    <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
      <Field label="Scope" inline>
        <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Filter by scope">
          <option value="all">All scopes</option>
          {scopes.map((s) => (
            <option key={s} value={s}>{scopeLabel(s)}</option>
          ))}
        </Select>
      </Field>
      <Field label="Search" inline>
        <form onSubmit={submitSearch} style={{ display: "flex", gap: "var(--space-2)" }}>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find in title or body"
            aria-label="Search notes"
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

  const filtered = scope !== "all" || applied.trim() !== "";

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load notes"
        hint={<><code>/api/notes</code> ({err})</>}
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
              <Skeleton width="80%" />
              <Skeleton width="30%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (notes.length === 0) {
    body = (
      <StateBlock
        title={filtered ? "No notes match these filters" : "No notes yet"}
        hint={
          filtered ? (
            <>Nothing matches. Reset <strong>Scope</strong> to <strong>All</strong> or clear the search to see every note.</>
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
            Showing the first {OVERFLOW_AT} of {notes.length} notes. Narrow by scope or search to see the rest.
          </p>
        )}
        <CardGrid className="ws-notes-grid">
          {shown.map((n) => (
            <li key={`${n.scope}:${n.id}`}>
              <Card
                interactive
                compact
                onClick={() => { setNotice(null); setOpen({ mode: "edit", note: n }); }}
                style={{ cursor: "pointer" }}
                className="ws-note-card"
              >
                <Stack gap="sm">
                  <span className="ws-note-card__title">{n.title || n.id}</span>
                  <span className="ws-note-card__hook">{n.hook || ""}</span>
                  <Row wrap className="ws-note-card__footer" style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                    <Tag hue={catFor(n.scope || "workshop")}>{scopeLabel(n.scope)}</Tag>
                    {n.updated && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(n.updated)}</span>
                    )}
                  </Row>
                </Stack>
              </Card>
            </li>
          ))}
        </CardGrid>
      </>
    );
  }

  return (
    <div>
      <PageHeader title="Notes" sub="Scoped markdown notes across every scope, read and written by the operator and agents alike." />
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Notes view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "docs" ? (
        <Documents />
      ) : tab === "vault" ? (
        <VaultNotes />
      ) : tab === "files" ? (
        <Files />
      ) : (
        <>
          {notice && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
          )}
          <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
          {body}
        </>
      )}
      {open?.mode === "create" && (
        <CreateNote
          scopes={scopes}
          initialScope={scope !== "all" ? scope : undefined}
          onClose={() => setOpen(null)}
          onCreated={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
      {open?.mode === "edit" && (
        <EditNote
          note={open.note}
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
          body: JSON.stringify({ title: title.trim(), content, hook: hook.trim() }),
        },
      );
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      onCreated(`Note saved to ${scopeLabel(scope)}.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [scope, title, hook, content, onCreated]);

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

// ---- Edit drawer -----------------------------------------------------------

type EditDraft = { title: string; hook: string; content: string };

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
  const [draft, setDraft] = useState<EditDraft>({ title: note.title, hook: note.hook, content: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Load the note body (the list carries only title/hook/updated).
  useEffect(() => {
    let live = true;
    setLoading(true);
    fetch(`/api/knowledge/${idPath}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!live) return;
        const bodyText = j?.content ?? "";
        setContent(bodyText);
        setDraft({ title: note.title, hook: note.hook, content: bodyText });
      })
      .catch(() => { if (live) setContent(""); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [idPath, note.title, note.hook]);

  const setField = <K extends keyof EditDraft>(k: K, v: EditDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const baseDraft: EditDraft = { title: note.title, hook: note.hook, content: content ?? "" };
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(baseDraft);

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch(`/api/knowledge/${idPath}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: draft.title.trim(), content: draft.content, hook: draft.hook.trim() }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setContent(draft.content);
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
              {note.updated && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{fmtTs(note.updated)}</span>}
            </Row>
            {note.hook && <p className="ws-muted" style={{ margin: 0 }}>{note.hook}</p>}
            {loading ? (
              <Skeleton width="70%" />
            ) : content && content.trim() ? (
              <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>{content}</p>
            ) : (
              <p className="ws-muted" style={{ margin: 0 }}>This note has no body yet.</p>
            )}
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

// ---- Documents (read-only docs/ index) -------------------------------------

// The read-only bridge to the committed docs/ tree (GET /api/documents). Plans and
// decision records stay versioned markdown; this view only indexes and opens
// them so the operator discovers documents alongside notes. No editing here.
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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

function Documents() {
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<DocsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openDoc, setOpenDoc] = useState<Doc | null>(null);

  // Open the doc a `?path=` deep link names, once. A resolved `workshop:doc/...`
  // ref (lib/refs.ts) points here so a doc chip opens the actual document
  // rather than landing on the tab and leaving the operator to find it again --
  // the same gesture `?note=` already does for notes above.
  const wantPath = searchParams.get("path");
  const openedRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/documents");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const docs = data?.docs ?? [];

  useEffect(() => {
    if (!wantPath || openedRef.current === wantPath || !docs.length) return;
    const hit = docs.find((d) => d.path === wantPath);
    if (!hit) return;
    openedRef.current = wantPath;
    setOpenDoc(hit);
  }, [wantPath, docs]);

  // Group by top-level section (loose top-level files go under "Top level").
  const groups = new Map<string, Doc[]>();
  for (const d of docs) {
    const key = d.section || "";
    const list = groups.get(key) ?? [];
    list.push(d);
    groups.set(key, list);
  }
  const sections = Array.from(groups.keys()).sort((a, b) => {
    if (a === "") return -1;
    if (b === "") return 1;
    return a.localeCompare(b);
  });

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load documents"
        hint={<><code>/api/documents</code> ({err})</>}
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
  } else if (docs.length === 0) {
    body = (
      <StateBlock
        title="No documents yet"
        hint="Long-form plans and decision records show up here once they are written."
      />
    );
  } else {
    body = (
      <Stack gap="lg">
        {sections.map((section) => (
          <div key={section || "top"}>
            <SectionLabel>{section || "Top level"}</SectionLabel>
            <CardGrid className="ws-notes-grid">
              {(groups.get(section) ?? []).map((d) => (
                <li key={d.path}>
                  <Card
                    interactive
                    compact
                    onClick={() => setOpenDoc(d)}
                    style={{ cursor: "pointer" }}
                    className="ws-doc-card"
                  >
                    <Stack gap="sm">
                      <Row style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <FileText size={14} aria-hidden />
                        <span className="ws-note-card__title">{d.title}</span>
                      </Row>
                      <span className="ws-doc-card__path">{d.path}</span>
                    </Stack>
                  </Card>
                </li>
              ))}
            </CardGrid>
          </div>
        ))}
      </Stack>
    );
  }

  return (
    <>
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Row wrap style={{ gap: "var(--space-3)" }}>
          <Button size="sm" onClick={refresh} loading={loading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </Row>
      </div>
      {body}
      {openDoc && <DocViewer doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </>
  );
}

function DocViewer({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setErr(null);
    fetch(`/api/documents/file?path=${encodeURIComponent(doc.path)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then((j) => { if (live) setContent(j?.content ?? ""); })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [doc.path]);

  return (
    <Modal
      open
      onClose={onClose}
      title={doc.title}
      size="lg"
      actions={<Button variant="primary" onClick={onClose}>Close</Button>}
    >
      <Stack gap="lg">
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", wordBreak: "break-all" }}>{doc.path}</span>
        {loading ? (
          <Stack gap="sm">
            <Skeleton width="80%" />
            <Skeleton width="90%" />
            <Skeleton width="60%" />
          </Stack>
        ) : err ? (
          <StateBlock variant="error" title="Couldn't load this document" hint={<code>{err}</code>} />
        ) : (
          <p style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontSize: "var(--fs-sm)" }}>{content}</p>
        )}
      </Stack>
    </Modal>
  );
}
