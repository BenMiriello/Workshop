// The Files tab: the kit's `FileTree` over one workspace, and the kit's
// CodeMirror editor for the file it selects. Both primitives landed 2026-08-22
// with docs/platform-redesign/file-viewer-and-editor-2026-08-22.md; this is
// that doc's P4, and it replaced the deliberately plain placeholder that stood
// here (a breadcrumb, a hand-rolled button list and a textarea).
//
// THERE IS NO VIEW/EDIT TOGGLE, and that is the design (sec 4): a file gets a
// rendered view only when its rendered form DIFFERS from its source. `main.py`
// has one form, so its highlighted source IS its view, and the editor is simply
// live on a file you may write. Markdown in Memory is the other case, and it
// draws THE SAME CHROME with a toggle in it: `DocumentBar` from the kit, whose
// state comes from the pure `documentBar()` rule, so the two surfaces cannot
// drift the way they had by 2026-08-22.
//
// THE HEADER CARRIES SAVE, AND THERE IS NO STICKY ActionBar HERE. Editing still
// commits explicitly, which is what frontend/CLAUDE.md rule 7 requires; the bar
// that carries the commit is the document's own header rather than a bar stuck
// to the bottom of the page. Shipping both would put two Saves and two "unsaved"
// markers on one screen, and on a phone an open document hides the bottom bar
// (shell-mockup-spec-2026-08-22.md section 6), which would leave the ActionBar's
// Save unreachable exactly when it mattered. The unsaved DOT in the header is
// the "unsaved" pill's replacement, and the dot going out is what says it saved.
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
import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Lock } from "lucide-react";
import {
  Badge, Button, Card, DocumentBar, Field, FileDrill, FileTree, Input, Modal, Notice,
  Skeleton, Stack, StateBlock, cx, documentBar, drillTrail, formatBytes, shortHttpError, timeAgo,
  useEditMode,
  type FileDrillTrail, type FileTreeAdapter, type FileTreeNode,
} from "@workshop/ui";
import { useDirectoryPage } from "../../../components/shell/directory";
import { Editor, languageForPath } from "@workshop/ui/editor";
import type { components } from "../../../api/schema.gen";
import type { AppRecord } from "../../../lib/apps";
import { useIsWide } from "../../../lib/useMediaQuery";
import { useContentTakeover, useDirectoryContent } from "../../../components/shell/takeover";
import { AppTabPage } from "./AppTabShell";
import { ancestorIdsFor, fileIconFor, fileNameOf, folderNodesFor, parentPath, viewModeFor } from "./filePath";

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
  onBack,
  backLabel,
  takeover,
}: {
  workspace: string;
  path: string;
  /** Back to the level this file is in. Present only on a phone, where it IS
   *  the header: the combined chevron-plus-filename control replaces the Up bar
   *  while a document is open (shell-mockup-spec-2026-08-22.md sections 4.1
   *  and 6). A desktop has the tree beside the file and needs no back. */
  onBack?: () => void;
  backLabel?: string;
  /** The document owns the whole content area. Same boolean the shell is told
   *  about, so the two halves of the takeover cannot disagree. */
  takeover?: boolean;
}) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
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
    setSaveErr(null);
    const r = await fetch("/api/files/content", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace, path, content: draft.content }),
    });
    if (!r.ok) throw new Error((await readFailure(r)).message);
    setFile((f) => (f ? { ...f, content: draft.content } : f));
  });

  const save = () => {
    void edit.save().catch((e: unknown) => {
      setSaveErr(e instanceof Error ? e.message : String(e));
    });
  };

  // The editor is live, so the first keystroke is what opens the edit. `begin`
  // seeds the draft from the current file and `set` applies over it: React
  // applies the functional update after the seed, so no keystroke is lost.
  const onChange = (next: string) => {
    if (!edit.editing) edit.begin();
    edit.set("content", next);
  };

  // The four states all wear the same header, so the way out of a file that
  // failed to load is exactly the way out of one that opened.
  const frame = (body: ReactNode, bar?: ReactNode, meta?: ReactNode) => (
    <div className={cx("ws-doc", takeover && "ws-doc--full")}>
      <DocumentBar
        name={fileNameOf(path)}
        path={parentPath(path)}
        dirty={edit.dirty}
        onBack={onBack}
        backLabel={backLabel}
        meta={meta}
      >
        {bar}
      </DocumentBar>
      <div className="ws-doc__body">{body}</div>
    </div>
  );

  if (err) {
    return frame(
      <StateBlock
        variant="error"
        title="Couldn't load this file"
        hint={<code>{endpoint} ({err})</code>}
        action={<Button size="sm" onClick={load}>Retry</Button>}
      />,
    );
  }
  if (!file) {
    return frame(<Card><Stack gap="sm"><Skeleton width="70%" /><Skeleton width="50%" /></Stack></Card>);
  }

  const mode = viewModeFor(file);
  const canWrite = mode === "editable";
  // A source file has ONE form, so `hasRenderedForm` is false and no toggle is
  // drawn. That is the rule in file-viewer section 4, not a special case here.
  const bar = documentBar({
    writable: canWrite, hasRenderedForm: false, form: "source", dirty: edit.dirty,
  });

  return frame(
    <Stack>
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
      {saveErr && (
        <Notice
          tone="error"
          detail={saveErr}
          action={<Button size="sm" variant="ghost" onClick={save} loading={edit.saving}>Retry</Button>}
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
          height={takeover ? undefined : "65vh"}
          ariaLabel={path}
        />
      )}
    </Stack>,
    <>
      {bar.discard && (
        <Button
          className="ws-docbar__discard"
          size="sm"
          variant="ghost"
          onClick={edit.cancel}
          disabled={edit.saving}
        >
          Discard
        </Button>
      )}
      {bar.save && (
        <Button
          size="sm"
          variant="primary"
          onClick={save}
          loading={edit.saving}
          disabled={!bar.saveEnabled}
        >
          Save
        </Button>
      )}
    </>,
    // The file's own facts, and the one line that says a path refuses writes.
    // Desktop only: on a phone the name has the width and this is not what you
    // opened the file to read.
    <>
      {mode === "readonly" && (
        <Badge><Lock size={12} strokeWidth={1.8} aria-hidden /> read only</Badge>
      )}
      <span>
        {formatBytes(file.size)}{file.modified ? ` \u00b7 edited ${timeAgo(file.modified)}` : ""}
      </span>
    </>,
  );
}

// ---- toolbar inside the directory panel ------------------------------------

function DirToolbar({
  filter,
  onFilter,
  onNew,
}: {
  filter: string;
  onFilter: (v: string) => void;
  onNew: () => void;
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const toggleFilter = () => {
    if (filterOpen) onFilter("");
    setFilterOpen((p) => !p);
  };
  return (
    <>
      <div className="ws-pagecol__dirtoolbar__row">
        <Button size="sm" onClick={onNew}>New</Button>
        <Button size="sm" variant="ghost" onClick={toggleFilter}>Filter</Button>
      </div>
      {filterOpen && (
        <div className="ws-pagecol__dirtoolbar__filter">
          <Input
            value={filter}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => onFilter(e.target.value)}
            placeholder="Filter files"
          />
        </div>
      )}
    </>
  );
}

function NewFileModal({
  open,
  onClose,
  workspace,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  workspace: string;
  onCreated: (path: string) => void;
}) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    if (!path.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/files/content", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace, path: path.trim(), content: "" }),
      });
      if (!r.ok) {
        const { message } = await readFailure(r);
        throw new Error(message);
      }
      onCreated(path.trim());
      setPath("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New file"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={() => { void create(); }}>Create</Button>
        </>
      }
    >
      {err && <Notice tone="error">{err}</Notice>}
      <Field label="Path">
        <Input
          data-autofocus
          value={path}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPath(e.target.value)}
          onKeyDown={(e: React.KeyboardEvent) => { if (e.key === "Enter") void create(); }}
          placeholder="src/hello.py"
        />
      </Field>
    </Modal>
  );
}

// ---- the tree ---------------------------------------------------------------
//
// TWO SHAPES, ONE ADAPTER. On a wide screen the tree lives in column two via
// the directory takeover (`useDirectoryPage`): the page column slides the list
// out and the tree in, and the file fills the content column beside it. On a
// phone the tree IS the page, drilled one level at a time with Up walking back
// out (docs/platform-redesign/navigation-shell-2026-08-20.md section 8.6).
//
// The wide screen used to render `DetailLayout` with the tree in the rail
// (right-hand card). That put the tree in the content area, not in column two,
// and violated shell-mockup-spec-2026-08-22.md section 5. Fixed 2026-08-23.
//
// It also used to render `DetailLayout` unconditionally, which on a phone
// stacked the rail above the main column: picker + file in one page scroll with
// the tree scrolling inside a 65vh card. Two nested scrollers, file below the
// fold. Reported 2026-08-22. The drill is a DIFFERENT TREE, not the same one
// restyled, which is exactly what `useIsWide` is for (lib/useMediaQuery.ts).
//
// Up is STRUCTURAL, not Back (section 8.1). From an open file it goes to the
// level that file is IN, whether you drilled there or arrived on a deep link,
// because the trail is rebuilt from the path either way.

function FilesBody({ app }: { app: AppRecord }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
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
          // THE PHONE ROW'S META (shell-mockup-spec-2026-08-22.md section 4.2):
          // a relative time on a file. A folder's is a CHILD COUNT, which this
          // API cannot answer without one listing request per folder, so the
          // meta is simply absent on a folder rather than being filled with a
          // size or a placeholder. The desktop tree draws no meta at all.
          detail: e.kind === "file" && e.modified ? timeAgo(e.modified, { suffix: false }) : undefined,
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

  // UP AT THE ROOT LEAVES THE DIRECTORY (mockup spec section 4.1). There is no
  // level above the workspace root, so the only honest destination is out of the
  // Files page: the app's own screen, which is the first row of this app's page
  // column (components/shell/places.ts) and the path these tab routes hang off.
  const leaveFiles = useCallback(() => { navigate(`/apps/${app.slug}`); }, [navigate, app.slug]);

  // Wide: tree in column two via directory takeover. The tree IS the navigator,
  // so it opens automatically (calling onNeedColumn to open the column too).
  const { host, toolbarHost, setOpen } = useDirectoryPage({
    pageId: "files",
    label: "Files",
    enabled: isWide,
    hasToolbar: true,
  });

  const [filter, setFilter] = useState("");
  const [newFileOpen, setNewFileOpen] = useState(false);

  useEffect(() => {
    if (isWide && host) setOpen(true);
  }, [isWide, host, setOpen]);

  // Filtered adapter: only wraps when a filter is typed. A new adapter object
  // resets the tree, which is acceptable for a deliberate filter action.
  const filteredAdapter = useMemo<FileTreeAdapter>(() => {
    if (!filter.trim()) return adapter;
    const term = filter.toLowerCase();
    return {
      root: adapter.root,
      children: async (node: FileTreeNode) => {
        const result = await adapter.children(node);
        return {
          ...result,
          nodes: result.nodes.filter((n: FileTreeNode) => n.label.toLowerCase().includes(term)),
        };
      },
    };
  }, [adapter, filter]);

  // The shell half of the takeover: while a file is open on a phone, the page
  // toolbar collapses to zero and its caret is not drawn (spec sections 1.1 and
  // 6). Declared here rather than inside FilePane so it is raised for exactly
  // the render that shows one, and never on a desktop, where the tree sits
  // beside the file and nothing is in the way.
  useContentTakeover(!isWide && !!selected);

  if (noTree) return <StateBlock title="No files here" hint={noTree} />;

  if (!isWide) {
    // The tree IS the page here. One level, no rail, no nested scroller.
    //
    // AN OPEN FILE IS A FULL CONTENT TAKEOVER (mockup spec section 6). The Up
    // bar is gone because the header's combined back-plus-filename control IS
    // the way out, the page toolbar collapses (the shell reads the declaration
    // above), and there is no bottom action bar because the header carries Save.
    return selected ? (
      <FilePane
        key={selected}
        workspace={workspace}
        path={selected}
        onBack={upFromFile}
        backLabel={upTo.label}
        takeover
      />
    ) : (
      <FileDrill
        adapter={adapter}
        trail={trail}
        onTrail={setTrail}
        onExit={leaveFiles}
        selectedId={selected}
        onSelect={onSelect}
        label={`${app.name} files`}
        emptyTitle="No files here"
        emptyHint="Add a file to this app's project tree and it shows up here."
      />
    );
  }

  // Wide: tree portalled into column two, file in the content area.
  return (
    <>
      {host && createPortal(
        <FileTree
          adapter={filteredAdapter}
          selectedId={selected}
          onSelect={onSelect}
          defaultOpenIds={openIds}
          label={`${app.name} files`}
          emptyTitle="No files here"
          emptyHint="Add a file to this app's project tree and it shows up here."
        />,
        host,
      )}
      {toolbarHost && createPortal(
        <DirToolbar filter={filter} onFilter={setFilter} onNew={() => setNewFileOpen(true)} />,
        toolbarHost,
      )}
      <NewFileModal
        open={newFileOpen}
        onClose={() => setNewFileOpen(false)}
        workspace={workspace}
        onCreated={(path) => {
          setNewFileOpen(false);
          setParams((prev) => {
            const next = new URLSearchParams(prev);
            next.set("path", path);
            return next;
          }, { replace: true });
        }}
      />
      {selected
        ? <FilePane key={selected} workspace={workspace} path={selected} />
        : <StateBlock title="No file open" hint="Pick a file in the tree to read or edit it." />
      }
    </>
  );
}

export default function AppFilesTab() {
  const { appSlug = "" } = useParams();
  const isWide = useIsWide();
  // THE TREE IS THE PAGE ON A PHONE, so there is no page header above it
  // (shell-mockup-spec-2026-08-22.md section 4; the rule and its reasoning are
  // in components/shell/pageChrome.ts). The h1 said "Files" under a tab already
  // reading "Files" and the trail said "Birds" under a bar one already reading
  // "Birds": ~290px of repeats before the first row, measured 2026-08-22.
  //
  // DECLARED HERE, NOT IN `FilesBody`, so it is true while the app record is
  // still loading. `FilesBody` only mounts once that resolves, and a header
  // that appears for the length of a fetch and then vanishes is worse than
  // either state on its own.
  useDirectoryContent(!isWide);
  return (
    <AppTabPage slug={appSlug} title="Files" sub="The app's linked project tree.">
      {(app) => <FilesBody app={app} />}
    </AppTabPage>
  );
}
