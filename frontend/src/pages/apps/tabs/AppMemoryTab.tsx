// The Memory tab: this app's notes (services/knowledge.py, scope `app:<slug>`),
// real for every app the same way Chats is -- no project link required.
// GET /api/apps/{id}/notes is the read side; writing goes through the same
// per-note PUT the /memory page and ProjectDetail/AgentDetail already use, so
// a note written here shows up in Memory too and vice versa.
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { StickyNote, Plus } from "lucide-react";
import { Button, Card, Input, Row, SectionLabel, Stack, Textarea, timeAbsoluteShort } from "@workshop/ui";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";

// The shape observed off services/knowledge.list_notes(), same convention as
// ProjectDetail.tsx's KnowledgeNote (the route has no response_model yet).
type AppNote = { id: string; title?: string; hook?: string; updated?: string | null };

function freshNoteId(title: string): string {
  const base =
    (title || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "note";
  return `${base}-${Date.now().toString(36)}`;
}

function MemoryBody({ app }: { app: AppRecord }) {
  const scope = `app:${app.slug}`;
  const [notes, setNotes] = useState<AppNote[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [postErr, setPostErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/notes`;

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      setNotes((d.notes ?? []) as AppNote[]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setNotes([]);
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  const post = useCallback(async () => {
    const t = title.trim();
    if (!t && !body.trim()) return;
    setPosting(true);
    setPostErr(null);
    try {
      const id = freshNoteId(t);
      const r = await fetch(`/api/knowledge/${encodeURIComponent(scope)}/notes/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: t, content: body, hook: "" }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      setTitle("");
      setBody("");
      await load();
    } catch (e) {
      setPostErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPosting(false);
    }
  }, [title, body, scope, load]);

  return (
    <section>
      <Row between wrap style={{ alignItems: "baseline" }}>
        <SectionLabel icon={<StickyNote aria-hidden />}>Memory</SectionLabel>
        <Link to={`/memory?scope=${encodeURIComponent(scope)}`} className="ws-bc__link">
          View all in Memory
        </Link>
      </Row>
      <Card>
        <Stack>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Note title" aria-label="Note title" />
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={`Leave a note for ${app.name} and its agents.`}
            rows={3}
          />
          <Row>
            <Button variant="primary" onClick={post} loading={posting} disabled={!title.trim() && !body.trim()}>
              <Plus aria-hidden /> Save note
            </Button>
          </Row>
          {postErr && <p className="ws-field__error">{postErr}</p>}

          <CollectionBody
            items={notes}
            err={err}
            reload={load}
            endpoint={endpoint}
            emptyTitle="No notes yet"
            emptyHint={`Notes here are shared with ${app.name}'s agents and go into its chat context.`}
            overflowHint="Consolidate or archive older notes from the Memory page."
            render={(rows) => (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {rows.map((n) => (
                  <li key={n.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                      <span style={{ fontWeight: 600, wordBreak: "break-word" }}>{n.title || n.id}</span>
                      {n.updated && (
                        <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{timeAbsoluteShort(n.updated)}</span>
                      )}
                    </Row>
                    {n.hook && (
                      <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-sm)", wordBreak: "break-word" }}>{n.hook}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          />
        </Stack>
      </Card>
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
