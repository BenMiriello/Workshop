// The Files tab: a file tree list and the simplest honest viewer/editor.
// GET /api/apps/{id}/files?path= lists one directory; GET .../files/content
// reads one file; PUT writes it back. A full editor (syntax highlighting,
// tabs, a diff view) is explicitly deferred to a third-party package later
// (design doc sec 8.1's own "we're not gonna build that all custom
// ourselves"), so this is deliberately plain: a breadcrumb, a list, and a
// textarea.
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Folder, File as FileIcon, ChevronRight, Pencil, ArrowLeft } from "lucide-react";
import { ActionBar, Button, Card, Notice, Row, SectionLabel, Skeleton, StateBlock, Stack, Textarea, formatBytes, useEditMode } from "@workshop/ui";
import type { components } from "../../../api/schema.gen";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage } from "./AppTabShell";
import { breadcrumbsFor, viewModeFor } from "./filePath";

type AppFileTree = components["schemas"]["AppFileTree"];
type AppFileContent = components["schemas"]["AppFileContent"];

function FileViewer({ appId, path, onBack, onSaved }: {
  appId: string;
  path: string;
  onBack: () => void;
  onSaved: () => void;
}) {
  const [file, setFile] = useState<AppFileContent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${appId}/files/content?path=${encodeURIComponent(path)}`;

  const load = useCallback(async () => {
    setErr(null);
    setFile(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      setFile(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  const edit = useEditMode({ content: file?.content ?? "" }, async (draft) => {
    const r = await fetch(`/api/apps/${appId}/files/content`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content: draft.content }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
    setFile((f) => (f ? { ...f, content: draft.content } : f));
    onSaved();
  });

  return (
    <section>
      <Row between wrap style={{ alignItems: "center", marginBottom: "var(--space-3)" }}>
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft aria-hidden /> Back to files
        </Button>
        <span className="ws-mono ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{path}</span>
      </Row>

      {err ? (
        <StateBlock
          variant="error"
          title="Couldn't load this file"
          hint={<code>{endpoint} ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : !file ? (
        <Card><Stack gap="sm"><Skeleton width="70%" /><Skeleton width="50%" /></Stack></Card>
      ) : (
        <Card>
          <Stack>
            <span className="ws-card__meta">
              {formatBytes(file.size)}{file.modified ? ` · modified ${file.modified}` : ""}
            </span>

            {viewModeFor(file) === "binary" && (
              <Notice tone="info">This file is binary. Binary files can't be shown here.</Notice>
            )}
            {viewModeFor(file) === "truncated" && (
              <Notice tone="info">
                This file is large and was truncated to {formatBytes(file.size)}. Editing is
                turned off so a partial read can't overwrite the full file.
              </Notice>
            )}
            {viewModeFor(file) === "readonly" && (
              <Notice tone="info">This file is read-only here.</Notice>
            )}

            {viewModeFor(file) === "binary" ? null : edit.editing ? (
              <>
                <Textarea
                  value={edit.draft.content}
                  onChange={(e) => edit.set("content", e.target.value)}
                  rows={24}
                  autoFocus
                  className="ws-mono"
                  style={{ fontSize: "var(--fs-sm)" }}
                  spellCheck={false}
                />
                <ActionBar flash={edit.flash ? "Saved" : undefined}>
                  <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
                  <Button variant="primary" onClick={() => { void edit.save().catch(() => {}); }} loading={edit.saving} disabled={!edit.dirty}>
                    Save
                  </Button>
                </ActionBar>
              </>
            ) : (
              <>
                <pre
                  className="ws-mono"
                  style={{
                    margin: 0, padding: "var(--space-3)", background: "var(--bg-inset)",
                    borderRadius: "var(--radius-sm)", fontSize: "var(--fs-sm)",
                    whiteSpace: "pre-wrap", overflow: "auto", maxHeight: "60vh",
                  }}
                >
                  {file.content || "(empty file)"}
                </pre>
                {viewModeFor(file) === "editable" && (
                  <Row>
                    <Button size="sm" variant="ghost" onClick={edit.begin}>
                      <Pencil aria-hidden /> Edit
                    </Button>
                  </Row>
                )}
              </>
            )}
          </Stack>
        </Card>
      )}
    </section>
  );
}

function FilesBody({ app }: { app: AppRecord }) {
  const [dir, setDir] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [tree, setTree] = useState<AppFileTree | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint = `/api/apps/${app.id}/files?path=${encodeURIComponent(dir)}`;

  const load = useCallback(async () => {
    setErr(null);
    setTree(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      setTree(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  if (selected) {
    return (
      <FileViewer
        appId={app.id}
        path={selected}
        onBack={() => setSelected(null)}
        onSaved={load}
      />
    );
  }

  const crumbs = breadcrumbsFor(dir);

  return (
    <section>
      <SectionLabel icon={<Folder aria-hidden />}>Files</SectionLabel>

      {err ? (
        <StateBlock
          variant="error"
          title="Couldn't load this directory"
          hint={<code>{endpoint} ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : !tree ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /><Skeleton width="50%" /></Stack></Card>
      ) : tree.reason ? (
        <StateBlock title="No file tree" hint={tree.reason} />
      ) : (
        <Card style={{ padding: "var(--space-3)" }}>
          <Stack>
            <Row wrap style={{ gap: "var(--space-1)", alignItems: "center", fontSize: "var(--fs-sm)" }}>
              <button type="button" className="ws-bc__link" onClick={() => setDir("")} style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}>
                {tree.root}
              </button>
              {crumbs.map((c) => (
                <span key={c.path} style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}>
                  <ChevronRight size={12} strokeWidth={1.8} aria-hidden className="ws-muted" />
                  <button type="button" className="ws-bc__link" onClick={() => setDir(c.path)} style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}>
                    {c.label}
                  </button>
                </span>
              ))}
            </Row>

            {tree.entries.length === 0 ? (
              <StateBlock title="Empty directory" hint="Nothing here." />
            ) : tree.entries.length > 500 ? (
              <StateBlock title="Too many entries to render" hint="This directory has more than 500 entries." />
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {tree.entries.map((e) => (
                  <li key={e.path} style={{ borderBottom: "1px solid var(--border)" }}>
                    <button
                      type="button"
                      onClick={() => (e.kind === "dir" ? setDir(e.path) : setSelected(e.path))}
                      style={{
                        display: "flex", alignItems: "center", gap: "var(--space-2)", width: "100%",
                        padding: "var(--space-2) 0", background: "none", border: "none", cursor: "pointer",
                        color: "inherit", textAlign: "left", font: "inherit",
                      }}
                    >
                      {e.kind === "dir"
                        ? <Folder size={14} strokeWidth={1.8} aria-hidden />
                        : <FileIcon size={14} strokeWidth={1.8} aria-hidden />}
                      <span style={{ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {e.name}
                      </span>
                      {e.kind === "file" && e.size != null && (
                        <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{formatBytes(e.size)}</span>
                      )}
                      {e.kind === "dir" && <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="ws-muted" />}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {tree.filtered && tree.filtered.length > 0 && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Hidden here: {tree.filtered.join(", ")}
              </span>
            )}
          </Stack>
        </Card>
      )}
    </section>
  );
}

export default function AppFilesTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Files" sub="The app's linked project tree.">
      {(app) => <FilesBody app={app} />}
    </AppTabPage>
  );
}
