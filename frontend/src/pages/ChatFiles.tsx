// Everything the operator has attached to a chat, newest first.
//
// WHY IT IS A PAGE UNDER CHATS AND NOT A TAB ON MEMORY. It used to be the
// fourth tab of the Memory page, behind a switch it shared with the notes
// library, the chat list and the notes app's vault: four unrelated collections
// on one page named after one of them, which is the thing the operator could
// not make sense of on 2026-08-27 ("why do we have library vault and files? I
// don't understand what these are or the separation of them"). Each went to the
// place that already owned it (memory-page-and-file-viewer-2026-08-27.md D1),
// and an attachment is a thing you put in a CHAT: this is the same place you go
// to find the conversation you put it in, filed beside Archived.
//
// Note this is chat attachments and NOT `routes/files.py`, which is the
// workspace file API an app's Files tab reads. The two have never been related
// and sharing the word "files" between them on one page was most of the
// confusion.
//
// A TABLE rather than the card grid the chat list uses: a file is a row of
// small facts (name, kind, size, scope, when), and cards would spend a whole
// tile saying very little. Four list states either way.
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RefreshCw, Trash2 } from "lucide-react";
import {
  Button, ConfirmDialog, Field, Input, PageHeader, Row, Select, Skeleton,
  Stack, StateBlock, StatusDot, Table, TableScroll, Tag, catFor, formatBytes,
  shortHttpError, timeAbsolute,
} from "@workshop/ui";
import AttachmentViewer from "../components/chat/AttachmentViewer";
import { scopeLabel } from "../lib/tasks";

async function httpFail(r: Response): Promise<Error> {
  return new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

/** A file the operator attached to a chat (GET /api/chat/attachments). */
type StoredFile = {
  id: string;
  name: string;
  kind: "image" | "pdf" | "text";
  size: number;
  scope: string;
  thread_id: string;
  created_at: string;
  /** The index has a row but the bytes are gone. Shown rather than hidden: a
   *  file that cannot be opened should say so where you go looking for it. */
  missing: boolean;
  /** A converted binary (docx/xlsx/pptx/archive): the row stores a text
   *  rendering AND the original bytes. `source` is its real type ("Excel
   *  spreadsheet"), shown instead of the bare "text" kind. */
  converted?: boolean;
  source?: string;
};
type FilesResp = { files: StoredFile[]; count: number; scopes: string[] };

/** Rows drawn before the list reports the rest instead of drawing it. */
const OVERFLOW_AT = 100;

export default function ChatFiles() {
  const [searchParams, setSearchParams] = useSearchParams();
  // ?file=<id> arrives from the attachment viewer's "Open in Files". It narrows
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
      <PageHeader
        title="Files"
        sub="Everything you have attached to a chat. The chat keeps the message; this is where the file itself lives."
      />
      <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
      {body}
      {viewing && (
        <AttachmentViewer
          file={{ id: viewing.id, name: viewing.name, kind: viewing.kind,
                  converted: viewing.converted, source: viewing.source }}
          onClose={() => setViewing(null)}
          onFilesPage
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
