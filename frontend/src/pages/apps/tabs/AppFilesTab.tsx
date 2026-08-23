// The Files tab: the kit's `FileTree` over one workspace, and the kit's
// CodeMirror editor for the file it selects. Both primitives landed 2026-08-22
// with docs/platform-redesign/file-viewer-and-editor-2026-08-22.md; this is
// that doc's P4, and it replaced the deliberately plain placeholder that stood
// here (a breadcrumb, a hand-rolled button list and a textarea).
//
// THERE IS NO VIEW/EDIT TOGGLE, and that is the design (sec 4): a file gets a
// rendered view only when its rendered form DIFFERS from its source. `main.py`
// has one form, so its highlighted source IS its view, and the editor is simply
// live on a file you may write. Markdown in Memory is the other case and a
// different ticket owns it. Editing still commits explicitly: typing opens the
// sticky ActionBar (frontend/CLAUDE.md rule 7), nothing saves by accident.
//
// TWO INDEPENDENT AXES, never conflated: `editable` is about the BYTES (text,
// small enough to open) and `writable` is about the LOCATION (does this
// workspace permit a write here). Both are the backend's answer. A file you may
// not write is not a broken text box: it opens, it highlights, it says "read
// only", and it never wears the refusal cursor design rule 10 forbids.
//
// It talks to the ONE file API (`/api/files/*`, backend/routes/files.py),
// addressed by workspace reference rather than by app id. The older
// `/api/apps/{id}/files*` routes are the same service through a second door and
// are parked for retirement in sec 8; nothing new should be built on them.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ChevronUp, FolderTree, Lock } from "lucide-react";
import {
  ActionBar, Badge, Button, Card, DetailLayout, FileDrill, FileTree, Notice, Row, SectionLabel,
  Skeleton, Stack, StateBlock, drillTrail, formatBytes, shortHttpError, timeAgo, useEditMode,
  type FileDrillTrail, type FileTreeAdapter, type FileTreeNode,
} from "@workshop/ui";
import { Editor, languageForPath } from "@workshop/ui/editor";
import type { components } from "../../../api/schema.gen";
import type { AppRecord } from "../../../lib/apps";
import { useIsWide } from "../../../lib/useMediaQuery";
import { AppTabPage } from "./AppTabShell";
import { ancestorIdsFor, fileIconFor, folderNodesFor, viewModeFor } from "./filePath";

type FileLevel = components["schemas"]["FileTree"];
type FileContent = components["schemas"]["FileContent"];

/** One failed response, read once, in the two forms this page needs: `message`
 *  for a reader debugging an endpoint (status + the server's sentence), and
 *  `reason` for the one place the status is noise because the 404 is an absence
 *  rather than a failure. `detail` arrives as a bare string from the edge shim
 *  and as `{code, message, hint}` from the backend (ADR 0023), the same two
 *  shapes `shortHttpError` unwraps. */
async function readFailure(r: Response): Promise<{ message: string; reason: string }> {
  const body = await r.text().catch(() => "");
  let reason = "";
  try {
    const detail = JSON.parse(body)?.detail;
    if (typeof detail === "string") reason = detail;
    else if (detail && typeof detail.message === "string") reason = detail.message;
  } catch {
    // not JSON: there is no sentence to pull out, only the status line below.
  }
  return { message: shortHttpError(r.status, r.statusText, body), reason: reason.trim() };
}

// ---- the file ---------------------------------------------------------------

function FilePane({
  workspace,
  path,
  actions,
}: {
  workspace: string;
  path: string;
  /** Chrome that belongs to the page rather than the file. On a phone this is
   *  the Up control, which is why it sits above the four states below and not
   *  only beside a file that loaded: a file that failed still has a level to
   *  walk back out to. */
  actions?: ReactNode;
}) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const endpoint =
    `/api/files/content?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(path)}`;

  const load = useCallback(async () => {
    setErr(null);
    setFile(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error((await readFailure(r)).message);
      setFile(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [endpoint]);

  useEffect(() => { load(); }, [load]);

  const edit = useEditMode({ content: file?.content ?? "" }, async (draft) => {
    const r = await fetch("/api/files/content", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace, path, content: draft.content }),
    });
    if (!r.ok) throw new Error((await readFailure(r)).message);
    setFile((f) => (f ? { ...f, content: draft.content } : f));
  });

  // The editor is live, so the first keystroke is what opens the edit. `begin`
  // seeds the draft from the current file and `set` applies over it: React
  // applies the functional update after the seed, so no keystroke is lost.
  const onChange = (next: string) => {
    if (!edit.editing) edit.begin();
    edit.set("content", next);
  };

  if (err) {
    return (
      <Stack gap="sm">
        {actions}
        <StateBlock
          variant="error"
          title="Couldn't load this file"
          hint={<code>{endpoint} ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      </Stack>
    );
  }
  if (!file) {
    return (
      <Stack gap="sm">
        {actions}
        <Card><Stack gap="sm"><Skeleton width="70%" /><Skeleton width="50%" /></Stack></Card>
      </Stack>
    );
  }

  const mode = viewModeFor(file);
  const canWrite = mode === "editable";

  return (
    <Stack>
      <Row between wrap>
        <Row>
          {actions}
          <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{path}</span>
        </Row>
        <Row>
          {mode === "readonly" && (
            <Badge><Lock size={12} strokeWidth={1.8} aria-hidden /> read only</Badge>
          )}
          <span className="ws-card__meta">
            {formatBytes(file.size)}{file.modified ? ` · edited ${timeAgo(file.modified)}` : ""}
          </span>
        </Row>
      </Row>

      {mode === "binary" && (
        <Notice tone="info">This file is binary, so it can't be shown here.</Notice>
      )}
      {mode === "truncated" && (
        <Notice tone="info">
          This file is too large to open here. Read it from a terminal or an agent chat.
        </Notice>
      )}
      {/* A save that failed leaves the editor on screen with the text still in
          it, so this is a line beside content, not a region that replaces it. */}
      {edit.saveError && (
        <Notice
          tone="error"
          detail={edit.saveError}
          action={<Button size="sm" variant="ghost" onClick={() => { void edit.save(); }} loading={edit.saving}>Retry</Button>}
        >
          Couldn't save this file.
        </Notice>
      )}

      {mode !== "binary" && mode !== "truncated" && (
        <Editor
          value={edit.editing ? edit.draft.content : file.content}
          onChange={canWrite ? onChange : undefined}
          path={path}
          editable={canWrite}
          height="65vh"
          ariaLabel={path}
        />
      )}

      {(edit.editing || edit.flash) && (
        <ActionBar flash={edit.flash ? "Saved" : undefined}>
          {edit.editing && (
            <>
              <Button variant="primary" onClick={() => { void edit.save(); }} loading={edit.saving} disabled={!edit.dirty}>
                Save
              </Button>
              <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
              {edit.dirty && <Badge>unsaved</Badge>}
            </>
          )}
        </ActionBar>
      )}
    </Stack>
  );
}

// ---- the tree ---------------------------------------------------------------
//
// TWO SHAPES, ONE ADAPTER. A wide screen gets the two-pane `DetailLayout`: the
// tree in the rail, the file beside it. A phone gets the tree AS THE PAGE,
// drilled one level at a time, with Up walking back out of it
// (docs/platform-redesign/navigation-shell-2026-08-20.md section 8.6, "the file
// tree is the page content and drills in place, with Up walking the tree").
//
// It used to render `DetailLayout` unconditionally, which on a phone stacks the
// rail above the main column: the picker and the file it picked ended up in one
// page scroll, with the tree additionally scrolling inside a 65vh card. Two
// nested scrollers, and the file you opened below the fold. Reported 2026-08-22.
// So the drill is a DIFFERENT TREE, not the same one restyled, which is exactly
// what `useIsWide` is for (see lib/useMediaQuery.ts): rendering both and hiding
// one in CSS would mount every row twice and fetch every level twice.
//
// Up is STRUCTURAL, not Back (section 8.1). From an open file it goes to the
// level that file is IN, whether you drilled there or arrived on a deep link,
// because the trail is rebuilt from the path either way.

function FilesBody({ app }: { app: AppRecord }) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("path");
  const workspace = `app:${app.slug}`;
  const isWide = useIsWide();
  // "This app has no linked project" is the workspace resolver's own answer and
  // it is an ABSENCE, not a failure: at the root (and only there) a 404 means
  // there is no tree to load rather than that loading it went wrong.
  const [noTree, setNoTree] = useState<string | null>(null);
  // Read once, at mount: a link straight to ?path=src/pages/x.tsx opens with
  // its folders expanded. Later selections open on the click that made them.
  const [openIds] = useState(() => ancestorIdsFor(params.get("path") ?? ""));

  // The root is not a row: its children are the top level, and its label is
  // what the tree's error state names.
  const root = useMemo<FileTreeNode>(
    () => ({ id: "", label: app.name, kind: "branch", icon: "folder" }),
    [app.name],
  );

  // THE PHONE'S TRAIL: which level the drill is listing, and the way back out
  // of it. It lives here rather than in the drill so that opening a file and
  // pressing Up returns to the level the file is in instead of the root.
  const [trail, setTrail] = useState<FileDrillTrail>(
    () => drillTrail(root, folderNodesFor(params.get("path") ?? "")),
  );

  const adapter = useMemo<FileTreeAdapter>(() => ({
    root,
    children: async (node) => {
      const url =
        `/api/files/tree?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(node.id)}`;
      const r = await fetch(url);
      if (!r.ok) {
        const { message, reason } = await readFailure(r);
        if (r.status === 404 && node.id === "") setNoTree(reason || message);
        // Thrown, not swallowed: the tree is what renders a failed level, as an
        // error region at the root and as a line beside the branches that are
        // still on screen anywhere below it.
        throw new Error(message);
      }
      const level: FileLevel = await r.json();
      return {
        nodes: level.entries.map((e) => ({
          id: e.path,
          label: e.name,
          kind: e.kind === "dir" ? "branch" : "leaf",
          icon: e.kind === "dir" ? "folder" : fileIconFor(e.path, languageForPath(e.path)),
          detail: e.kind === "file" && e.size != null ? formatBytes(e.size) : undefined,
        })),
        filtered: level.filtered ?? undefined,
      };
    },
  }), [workspace, root]);

  useEffect(() => { setNoTree(null); }, [workspace]);

  const onSelect = useCallback((node: FileTreeNode) => {
    // A branch opens where it stands. Only a file is a destination here.
    if (node.kind !== "leaf") return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("path", node.id);
      return next;
    }, { replace: true });
  }, [setParams]);

  // Up out of an open file, on a phone: back to the folder that holds it. The
  // trail is rebuilt from the path, so a deep link walks up correctly on its
  // first press without having been drilled into.
  const folders = useMemo(() => folderNodesFor(selected ?? ""), [selected]);
  const upTo = folders[folders.length - 1] ?? root;
  const upFromFile = useCallback(() => {
    setTrail(drillTrail(root, folders));
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("path");
      return next;
    }, { replace: true });
  }, [root, folders, setParams]);

  if (noTree) return <StateBlock title="No files here" hint={noTree} />;

  if (!isWide) {
    // The tree IS the page here. One level, no rail, no nested scroller.
    return selected ? (
      <FilePane
        key={selected}
        workspace={workspace}
        path={selected}
        actions={
          <Button size="sm" variant="ghost" onClick={upFromFile} aria-label={`Up to ${upTo.label}`}>
            <ChevronUp aria-hidden /> Up
          </Button>
        }
      />
    ) : (
      <FileDrill
        adapter={adapter}
        trail={trail}
        onTrail={setTrail}
        selectedId={selected}
        onSelect={onSelect}
        label={`${app.name} files`}
        emptyTitle="No files here"
        emptyHint="Add a file to this app's project tree and it shows up here."
      />
    );
  }

  return (
    <DetailLayout
      rail={
        <Stack gap="sm">
          <SectionLabel icon={<FolderTree aria-hidden />}>Files</SectionLabel>
          <Card compact style={{ maxHeight: "65vh", overflow: "auto" }}>
            <FileTree
              adapter={adapter}
              selectedId={selected}
              onSelect={onSelect}
              defaultOpenIds={openIds}
              label={`${app.name} files`}
              emptyTitle="No files here"
              emptyHint="Add a file to this app's project tree and it shows up here."
            />
          </Card>
        </Stack>
      }
      main={
        selected
          ? <FilePane key={selected} workspace={workspace} path={selected} />
          : <StateBlock title="No file open" hint="Pick a file in the tree to read or edit it." />
      }
    />
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
