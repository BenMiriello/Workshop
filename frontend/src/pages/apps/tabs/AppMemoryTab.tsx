// The Memory tab: this app's notes (services/knowledge.py, scope `app:<slug>`),
// real for every app the same way Chats is -- no project link required.
// GET /api/apps/{id}/notes is the read side; writing goes through the same
// per-note PUT the /memory page and ProjectDetail/AgentDetail already use, so
// a note written here shows up in Memory too and vice versa.
//
// A ROW OPENS WHERE IT IS LISTED. Until 2026-08-23 the rows were `<li><span>`,
// so nothing on this tab opened anything, and the only navigation was a
// "View all in Memory" link at the top of the page. The operator's objection
// was that a per-app surface should not hand you off to the global one, and the
// reason it did is that the tab had no read surface at all: the link existed
// BECAUSE nothing here opened. Every sibling tab (AppTicketsTab, AppAgentsTab)
// makes the ROW the link to the record; this was the only one that made the
// PAGE a link to a different page. A row is a `Link` now, exactly like a ticket
// row, and the link out is gone rather than restyled.
//
// Also measured that day: an empty tab led with a compose form sitting ABOVE a
// "No notes yet" panel, so it gave two answers to "is there anything here". The
// list leads now and writing is reached from the empty state's own action or
// the control beside the list, which is how Memory's own "New note" works.
//
// `?note=<id>` is the address, matching how AppFilesTab carries `?path=`, so a
// note is deep-linkable and the browser Back button walks out of it.
import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../../lib/queryKeys";
import { ChevronLeft, Plus, StickyNote } from "lucide-react";
import {
  Button, Card, CardGrid, Field, Input, Modal, Notice, Row, Stack, Textarea, timeAbsoluteShort,
} from "@workshop/ui";
import type { AppRecord } from "../../../lib/apps";
import { NoteDocument } from "../../memory/MemoryDocument";
import { AppTabPage, CollectionBody } from "./AppTabShell";

// The shape observed off services/knowledge.list_notes(), same convention as
// ProjectDetail.tsx's KnowledgeNote (the route has no response_model yet).
type AppNote = { id: string; title?: string; hook?: string; updated?: string | null };

function freshNoteId(title: string): string {
  const base =
    (title || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "note";
  return `${base}-${Date.now().toString(36)}`;
}

/** The compose form, reached from a control rather than stacked above the list.
 *  It is a WRITE, so it is deliberate; it is not the resting state of a page
 *  whose job is to show what is already there. */
function NewNoteDialog({
  app, scope, onClose, onCreated,
}: {
  app: AppRecord;
  scope: string;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = useCallback(async () => {
    const t = title.trim();
    if (!t && !body.trim()) return;
    setPosting(true);
    setErr(null);
    try {
      const id = freshNoteId(t);
      const r = await fetch(
        `/api/memory/scopes/${encodeURIComponent(scope)}/notes/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: t, content: body, hook: "" }),
        },
      );
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      onCreated(id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPosting(false);
    }
  }, [title, body, scope, onCreated]);

  return (
    <Modal open onClose={onClose} title="New note">
      <Stack>
        {err && <Notice tone="error">{err}</Notice>}
        <Field label="Title">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What is this about"
            aria-label="Note title"
            autoFocus
          />
        </Field>
        <Field label="Note">
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={`Leave a note for ${app.name} and its agents.`}
            rows={6}
          />
        </Field>
        <Row>
          <Button
            variant="primary"
            onClick={save}
            loading={posting}
            disabled={!title.trim() && !body.trim()}
          >
            Save note
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </Row>
      </Stack>
    </Modal>
  );
}

function MemoryBody({ app }: { app: AppRecord }) {
  const scope = `app:${app.slug}`;
  const endpoint = `/api/apps/${app.id}/notes`;
  const qc = useQueryClient();
  const [composing, setComposing] = useState(false);
  const [params, setParams] = useSearchParams();
  const openId = params.get("note");

  const q = useQuery({
    queryKey: queryKeys.apps.notes(app.id),
    queryFn: async (): Promise<AppNote[]> => {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      return (d.notes ?? []) as AppNote[];
    },
  });

  const invalidateNotes = useCallback(
    () => void qc.invalidateQueries({ queryKey: queryKeys.apps.notes(app.id) }),
    [qc, app.id]
  );

  const close = useCallback(() => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("note");
      return next;
    }, { replace: true });
  }, [setParams]);

  const openNote = useCallback((id: string) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("note", id);
      return next;
    }, { replace: true });
  }, [setParams]);

  // An open note takes the pane, the same way an open file does on the Files
  // tab. Back returns to the list, which is reloaded because the note may have
  // been retitled or deleted while it was open.
  if (openId) {
    return (
      <section>
        <NoteDocument
          key={`${scope}/${openId}`}
          scope={scope}
          noteId={openId}
          onChanged={invalidateNotes}
          onDeleted={() => { invalidateNotes(); close(); }}
          actions={
            <Button onClick={close} aria-label={`Back to ${app.name} memory`}>
              <ChevronLeft aria-hidden /> Memory
            </Button>
          }
        />
      </section>
    );
  }

  const newNote = (
    <Button variant="primary" onClick={() => setComposing(true)}>
      <Plus aria-hidden /> New note
    </Button>
  );

  return (
    <section>
      {composing && (
        <NewNoteDialog
          app={app}
          scope={scope}
          onClose={() => setComposing(false)}
          onCreated={(id) => { setComposing(false); invalidateNotes(); openNote(id); }}
        />
      )}
      <CollectionBody
        items={q.data ?? null}
        err={q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null}
        reload={() => void q.refetch()}
        endpoint={endpoint}
        emptyTitle="No notes yet"
        emptyHint={`Notes here are shared with ${app.name}'s agents and go into its chat context.`}
        emptyAction={newNote}
        overflowHint="Browse them all from the Memory page."
        render={(rows) => (
          <Stack gap="sm">
            <Row between wrap style={{ alignItems: "baseline" }}>
              <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                {rows.length} {rows.length === 1 ? "note" : "notes"}
              </span>
              {newNote}
            </Row>
            <CardGrid>
              {rows.map((n) => (
                <li key={n.id}>
                  <Link
                    to={`/apps/${app.slug}/memory?note=${encodeURIComponent(n.id)}`}
                    style={{ textDecoration: "none", color: "inherit" }}
                  >
                    <Card interactive>
                      <Row between style={{ gap: "var(--space-2)" }}>
                        <span
                          className="ws-card__title"
                          style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        >
                          <StickyNote size={14} aria-hidden /> {n.title || n.id}
                        </span>
                        {n.updated && (
                          <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", whiteSpace: "nowrap" }}>
                            {timeAbsoluteShort(n.updated)}
                          </span>
                        )}
                      </Row>
                      {n.hook && (
                        <p
                          className="ws-muted"
                          style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-sm)", wordBreak: "break-word" }}
                        >
                          {n.hook}
                        </p>
                      )}
                    </Card>
                  </Link>
                </li>
              ))}
            </CardGrid>
          </Stack>
        )}
      />
    </section>
  );
}

export default function AppMemoryTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Memory" sub="Notes shared with this app's agents and injected into its chat context.">
      {(app) => <MemoryBody app={app} />}
    </AppTabPage>
  );
}
