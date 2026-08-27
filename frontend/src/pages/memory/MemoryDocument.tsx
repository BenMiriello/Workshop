// A Memory document, in its two forms.
//
// THE RULE (file-viewer-and-editor-2026-08-22.md section 4): "A file gets a
// rendered view when its rendered form differs from its source. Otherwise its
// source is its view." A markdown memory has two forms, so it opens RENDERED and
// an Edit control switches to the kit editor showing the SOURCE. That is the
// only reason there is a toggle here at all, and it is why an app's source file
// does not get one.
//
// The two axes are independent, and conflating them is what produces the
// disabled-looking editor nobody can explain:
//
//   form      rendered | source   the operator picks, and only for a file that
//                                 HAS a rendered form
//   editable  true | false        the BACKEND decides, from whether the path is
//                                 writable
//
// So a read-only document is not "stuck in view mode": it is a file you may not
// write, it says so in one muted line, and it never shows a dead Edit button or
// a refusal cursor (design rule 10).
//
// Markdown is not the only thing with two forms. An image or a PDF in this
// workspace has a rendered form and no readable source, so the rendered form is
// `FilePreview` rather than `Markdown`. Before that, opening one gave an empty
// document under a line saying the file was not text.
//
// WRITABILITY IS THE BACKEND'S ANSWER NOW. `GET /api/files/content?workspace=
// documents` reports `writable` per path from the `documents` workspace's own
// `write_prefixes` (services/app_files.py). The `path.startsWith("docs/")` rule
// that used to sit in Memory.tsx is gone: it was always the backend's rule,
// written down twice.
//
// The pattern for the split itself is frontend/src/pages/apps/notes/NoteView.tsx,
// which is where this app already did it correctly: `useEditMode` for the draft,
// the kit `Editor` while editing, `Markdown` when not.
//
// ---- ONE SHAPE, FOR A NOTE AND FOR A FILE ---------------------------------
//
// BOTH wear the kit's `DocumentBar`: the same header the Files tab draws,
// measured in shell-mockup-spec-2026-08-22.md section 6, with the same pure
// `documentBar()` rule deciding what is in it. A file gets `variant="file"`
// (its name in mono, its directory ahead of it); a note gets `variant="title"`
// (its title in the sans face, its scope ahead of it, and the title editable in
// place). Everything else is identical.
//
// THIS FILE USED TO ARGUE THE OPPOSITE, and the argument is worth keeping
// because it is the kind that sounds right. It said: a note is a RECORD, not a
// file; it has no path and no filename, it has a title, a hook, a type, a
// pinned flag, an author and backlinks, and its edit mode is a FORM over five
// fields of which the markdown body is one. So it kept the record-page shape
// and a sticky `ActionBar`, and giving it a filename header "would have meant
// printing its title twice, in mono, above itself".
//
// The operator overruled it on 2026-08-27, and the reasoning holds up: the mono
// face was the real objection and a variant answers it, the other four fields
// are FIELDS and belong in the body with the rest of the body, and what a reader
// wants is one viewer and one editor rather than two that differ for reasons
// only this file knows. See memory-page-and-file-viewer-2026-08-27.md D7.
//
// THE HEADER CARRIES THE ACTIONS, and there is no sticky `ActionBar` on either
// shape now. Rule 7 requires an EXPLICIT COMMIT for a content write, not one
// particular container for it; the header is that commit, permanently on screen
// because it is sticky. Two Saves and two "unsaved" markers on one screen is the
// duplication this repo keeps deleting, and on a phone an open document hides
// the bottom bar (spec section 6), so the ActionBar's Save would be unreachable
// exactly when it mattered. The unsaved DOT replaces the "unsaved" pill, and the
// dot going out is the receipt.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Eye, Lock, Pencil, Pin, Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  DocumentBar,
  Field,
  Input,
  Notice,
  Row,
  SectionLabel,
  Select,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  Tag,
  catFor,
  documentBar,
  timeAbsolute,
  useEditMode,
  type DocumentForm,
} from "@workshop/ui";
import { Markdown } from "@workshop/ui/markdown";
import { Editor } from "@workshop/ui/editor";
import type { components } from "../../api/schema.gen";
import { FilePreview } from "../../components/FilePreview";
import { Ref } from "../../components/Ref";
import { previewKindFor } from "../../lib/filePreview";
import { backlinksFor } from "../../lib/refs";
import { scopeHref, scopeLabel } from "../../lib/tasks";
import { useResolvedRefs } from "../../lib/useResolvedRefs";
import { getJson, sendJson } from "./api";
import { baseName, dirName } from "./memoryTree";
import { NOTE_TYPES, authorLabel, chatRefsIn, noteTypeLabel, type NoteType } from "./notes";

type NoteDetail = components["schemas"]["NoteDetail"];
type NoteSummary = components["schemas"]["NoteSummary"];
type FileContent = components["schemas"]["FileContent"];

// ---- Shared pieces ---------------------------------------------------------

/** "Referenced by" on a note (memory-and-documentation-model sec 10:
 *  "backlinks fall out"). An index read (GET /api/refs/backlinks), never a
 *  corpus scan, and one batch resolve so N backlinks cost one request. Renders
 *  nothing when there are none: an inline "no backlinks" line would out-noise
 *  the body on every note written before this existed. */
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
        {sources.map((r) => <Ref key={r} resolved={resolved[r]} refStr={r} />)}
      </Row>
    </Stack>
  );
}

/** Author, timestamp, and the originating chat when the body names one. Never a
 *  raw path or a copyable URL (that doc's own acceptance test): a chat ref
 *  renders as a `<Ref>` chip that navigates. */
function Provenance({ author, updated, body }: { author?: string; updated?: string | null; body: string }) {
  const chatRefs = useMemo(() => chatRefsIn(body), [body]);
  const resolved = useResolvedRefs(chatRefs);
  return (
    <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{authorLabel(author)}</span>
      {updated && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{timeAbsolute(updated)}</span>}
      {chatRefs.map((r) => <Ref key={r} resolved={resolved[r]} refStr={r} />)}
    </Row>
  );
}

function LoadingDocument() {
  return (
    <Stack gap="sm">
      <Skeleton width="40%" />
      <Skeleton width="90%" />
      <Skeleton width="85%" />
      <Skeleton width="60%" />
    </Stack>
  );
}

// ---- A note ----------------------------------------------------------------

type NoteLoaded = {
  title: string;
  hook: string;
  updated: string | null;
  content: string;
  author: string;
  type: NoteType;
  pinned: boolean;
};

type NoteDraft = { title: string; hook: string; content: string; type: NoteType; pinned: boolean };

export function NoteDocument({
  scope,
  noteId,
  onChanged,
  onDeleted,
  actions,
}: {
  scope: string;
  noteId: string;
  /** A write landed: the tree and any list above it are now out of date. */
  onChanged: () => void;
  onDeleted: () => void;
  /** Controls the page wants beside this document's own (Up, on a phone). */
  actions?: React.ReactNode;
}) {
  const base = `/api/knowledge/${encodeURIComponent(scope)}/notes`;
  const [loaded, setLoaded] = useState<NoteLoaded | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Which form is on screen. Independent of whether an edit is open, exactly as
  // it is for a document: `form` is the reader's choice, and switching back to
  // the rendered view does not throw a draft away, it previews it.
  const [form, setForm] = useState<DocumentForm>("rendered");

  // Two reads, because the store answers two questions in two places: the
  // per-note GET carries the body and the provenance, and title/hook/updated
  // live in the scope's index. Both are needed before an edit can be saved --
  // the write route takes title and hook, so saving without them would blank
  // them.
  useEffect(() => {
    let live = true;
    setLoaded(null);
    setErr(null);
    setForm("rendered");
    Promise.all([
      getJson<NoteDetail>(`${base}/${encodeURIComponent(noteId)}`),
      getJson<NoteSummary[]>(base),
    ]).then(
      ([detail, index]) => {
        if (!live) return;
        const row = index.find((n) => n.id === noteId);
        setLoaded({
          title: row?.title ?? noteId,
          hook: row?.hook ?? "",
          updated: row?.updated ?? null,
          content: detail.content ?? "",
          author: detail.author ?? "",
          type: ((detail.type as NoteType) || "fact"),
          pinned: !!detail.pinned,
        });
      },
      (e: unknown) => { if (live) setErr(e instanceof Error ? e.message : String(e)); },
    );
    return () => { live = false; };
  }, [base, noteId, attempt]);

  const current: NoteDraft = useMemo(
    () => ({
      title: loaded?.title ?? "",
      hook: loaded?.hook ?? "",
      content: loaded?.content ?? "",
      type: loaded?.type ?? "fact",
      pinned: loaded?.pinned ?? false,
    }),
    [loaded],
  );

  const onSave = useCallback(
    async (draft: NoteDraft) => {
      setSaveErr(null);
      try {
        await sendJson(`${base}/${encodeURIComponent(noteId)}`, "PUT", {
          title: draft.title.trim(),
          content: draft.content,
          hook: draft.hook.trim(),
          // `author` is deliberately omitted: an edit does not reassign who
          // wrote the note. The two facets ARE sent, since the operator just
          // chose them.
          note_type: draft.type,
          pinned: draft.pinned,
        });
      } catch (e) {
        setSaveErr(e instanceof Error ? e.message : String(e));
        // Rethrown so `useEditMode` keeps the draft and stays in edit mode: a
        // save that failed must not look like one that worked.
        throw e;
      }
      setLoaded((prev) => (prev ? { ...prev, ...draft, title: draft.title.trim(), hook: draft.hook.trim() } : prev));
      onChanged();
    },
    [base, noteId, onChanged],
  );

  const edit = useEditMode<NoteDraft>(current, onSave);

  const del = useCallback(async () => {
    setDeleting(true);
    try {
      await sendJson(`${base}/${encodeURIComponent(noteId)}`, "DELETE");
      setConfirmDel(false);
      onDeleted();
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
      setConfirmDel(false);
    } finally {
      setDeleting(false);
    }
  }, [base, noteId, onDeleted]);

  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't load this note"
        hint={err}
        action={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
      />
    );
  }
  if (!loaded) return <LoadingDocument />;

  const bar = documentBar({ writable: true, hasRenderedForm: true, form, dirty: edit.dirty });
  const title = edit.editing ? edit.draft.title : loaded.title;
  const body = edit.editing ? edit.draft.content : loaded.content;
  const hook = edit.editing ? edit.draft.hook : loaded.hook;
  const href = scopeHref(scope);

  const goForm = (to: DocumentForm) => {
    // Entering the source form IS entering the edit, the same as a document.
    if (to === "source" && !edit.editing) edit.begin();
    setForm(to);
  };

  return (
    <div className="ws-doc ws-doc--full">
      {/* THE HEADER IS THE TITLE, and nothing else (2026-08-27, D7). It used to
          be a Row carrying an h2, a scope Tag, the type word, a Pinned dot and
          three buttons, above a page that ALSO drew its own header and search
          row; the operator asked for it "stripped down to just the name, the
          title of it". The `title` variant draws it in the sans face, and in
          edit mode it is an input, which is the only place a note's title can
          be changed: there is no rename route for a note anywhere.

          This is the same bar a document wears. The 2026-08-22 design kept the
          two apart on the grounds that a note is a record and not a file; what
          a reader actually wants is one viewer and one editor, and everything
          a note has that a file does not is a field, which belongs in the body
          with the other fields. */}
      <DocumentBar
        name={title || noteId}
        variant="title"
        onNameChange={edit.editing ? (v) => edit.set("title", v) : undefined}
        path={scopeLabel(scope)}
        dirty={edit.dirty}
      >
        {actions}
        {bar.toggle && (
          <Button size="sm" variant="ghost" onClick={() => goForm(bar.toggle!.to)}>
            {bar.toggle.to === "source"
              ? <><Pencil aria-hidden /> {bar.toggle.label}</>
              : <><Eye aria-hidden /> {bar.toggle.label}</>}
          </Button>
        )}
        {bar.discard && (
          <Button
            className="ws-docbar__discard"
            size="sm"
            variant="ghost"
            disabled={edit.saving}
            onClick={() => { edit.cancel(); setForm("rendered"); }}
          >
            Discard
          </Button>
        )}
        {bar.save && (
          <Button
            size="sm"
            variant="primary"
            loading={edit.saving}
            disabled={!bar.saveEnabled}
            onClick={() => { void edit.save().catch(() => {}); }}
          >
            Save
          </Button>
        )}
        {!edit.editing && (
          <Button
            size="sm"
            variant="danger-ghost"
            onClick={() => setConfirmDel(true)}
            aria-label="Delete note"
            title="Delete note"
            icon
          >
            <Trash2 aria-hidden />
          </Button>
        )}
      </DocumentBar>

      <div className="ws-doc__body">
        <Stack gap="lg">
          {saveErr && <Notice tone="error" detail={saveErr}>That didn't save.</Notice>}

          {form === "source" ? (
            <>
              {/* THE NOTE'S OTHER FIELDS. They are only on screen while you are
                  editing, because reading a note is reading its body: the type
                  and the pinned flag are already stated below it, in the one
                  muted line that carries the provenance. */}
              <Field
                label="Hook"
                htmlFor="memory-note-hook"
                optional
                help="A one-line summary shown in the index and lists."
              >
                <Input
                  id="memory-note-hook"
                  value={hook}
                  onChange={(e) => edit.set("hook", e.target.value)}
                />
              </Field>
              <Row wrap style={{ gap: "var(--space-4)", alignItems: "flex-end" }}>
                <Field label="Type" htmlFor="memory-note-type" inline>
                  <Select
                    id="memory-note-type"
                    value={edit.draft.type}
                    onChange={(e) => edit.set("type", e.target.value as NoteType)}
                  >
                    {NOTE_TYPES.map((t) => (
                      <option key={t} value={t}>{noteTypeLabel(t)}</option>
                    ))}
                  </Select>
                </Field>
                <Checkbox checked={edit.draft.pinned} onChange={(v) => edit.set("pinned", v)} label="Pinned" />
              </Row>
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Pinned notes, then preferences and decisions, rank first in what gets read into every chat in this scope.
              </p>
              <Editor
                value={body}
                onChange={(v) => {
                  // Live, so the first keystroke is what opens the edit.
                  if (!edit.editing) edit.begin();
                  edit.patch({ content: v });
                }}
                path="note.md"
                profile="prose"
                ariaLabel="Note body, markdown source"
              />
            </>
          ) : (
            <>
              {hook && <p className="ws-memory__hook">{hook}</p>}
              {body.trim() ? (
                <Markdown>{body}</Markdown>
              ) : (
                <p className="ws-muted" style={{ margin: 0 }}>This note has no body yet. Edit it to write one.</p>
              )}
              {/* WHAT THIS NOTE IS AND WHERE IT CAME FROM, under it rather than
                  over it. The scope is a link because it is the one thing here
                  that goes somewhere. */}
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                {href ? (
                  <Link to={href} className="ws-bc__link">
                    <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
                  </Link>
                ) : (
                  <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
                )}
                <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{noteTypeLabel(loaded.type)}</span>
                {loaded.pinned && (
                  <StatusDot status="ok"><Pin size={12} strokeWidth={1.8} aria-hidden /> Pinned</StatusDot>
                )}
              </Row>
              <Provenance author={loaded.author} updated={loaded.updated} body={loaded.content} />
              <ReferencedBy scope={scope} noteId={noteId} />
              {edit.flash && <p className="ws-muted" style={{ margin: 0 }}>Saved.</p>}
            </>
          )}
        </Stack>
      </div>

      <ConfirmDialog
        open={confirmDel}
        onCancel={() => setConfirmDel(false)}
        onConfirm={del}
        busy={deleting}
        title="Delete this note?"
        body={<>This removes the note from <strong>{scopeLabel(scope)}</strong>. It can't be undone.</>}
        confirmLabel="Delete note"
      />
    </div>
  );
}

// ---- A document ------------------------------------------------------------

export function DocDocument({
  path,
  onChanged,
  onBack,
  backLabel,
  takeover,
}: {
  path: string;
  onChanged: () => void;
  /** Back to the level this document is in. Present only on a phone, where the
   *  combined chevron-plus-filename control in the header IS the way out and
   *  replaces the Up bar (shell-mockup-spec-2026-08-22.md sections 4.1 and 6). */
  onBack?: () => void;
  backLabel?: string;
  /** The PHONE takeover: the shell is also collapsing the page toolbar and
   *  hiding the Up bar, and this is the same boolean, so the two halves cannot
   *  disagree. It no longer decides the document's LAYOUT: `ws-doc--full` is
   *  unconditional now (D8), because a document owns the content area on a
   *  desktop too. What it still decides is the phone-only editor sizing, which
   *  the class handles, and the back control in the header. */
  takeover?: boolean;
}) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // WHICH FORM IS ON SCREEN, and it is independent of whether an edit is open
  // (file-viewer-and-editor-2026-08-22.md section 4: `form` is the reader's
  // choice, `editable` is the backend's fact). Switching back to the rendered
  // view therefore does NOT throw a draft away: it renders what you have
  // written so far, and Save stays in the header while it is dirty. A toggle
  // that silently discarded an edit would need a confirm dialog to be safe, and
  // this needs none.
  const [form, setForm] = useState<DocumentForm>("rendered");

  useEffect(() => {
    let live = true;
    setFile(null);
    setErr(null);
    // The workspace file API, which is where `writable` is answered. The docs
    // read route (GET /api/documents/file) would give the bytes and nothing
    // else, and the frontend would be back to guessing.
    getJson<FileContent>(
      `/api/files/content?workspace=documents&path=${encodeURIComponent(path)}`,
    ).then(
      (j) => { if (live) setFile(j); },
      (e: unknown) => { if (live) setErr(e instanceof Error ? e.message : String(e)); },
    );
    return () => { live = false; };
  }, [path, attempt]);

  const current = useMemo(() => ({ content: file?.content ?? "" }), [file]);

  const onSave = useCallback(
    async (draft: { content: string }) => {
      setSaveErr(null);
      try {
        // The docs write route, unchanged: it writes into the working tree and
        // does not commit, which is the contract the copy below states.
        await sendJson("/api/documents/file", "PUT", { path, content: draft.content });
      } catch (e) {
        setSaveErr(e instanceof Error ? e.message : String(e));
        throw e;
      }
      setFile((prev) => (prev ? { ...prev, content: draft.content } : prev));
      onChanged();
    },
    [path, onChanged],
  );

  const edit = useEditMode(current, onSave);

  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't load this document"
        hint={err}
        action={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
      />
    );
  }
  if (!file) return <LoadingDocument />;

  // Two different facts, kept apart (section 4). `writable` is about the
  // LOCATION and comes from the workspace; `editable` and `truncated` are about
  // the BYTES. Where a write is refused there is no dimmed Edit control and no
  // refusal cursor: the header says "read only" and one muted line says why.
  const canEdit = file.writable && file.editable && !file.truncated;
  const bar = documentBar({ writable: canEdit, hasRenderedForm: true, form, dirty: edit.dirty });
  // The draft is the document once an edit is open, in EITHER form.
  const body = edit.editing ? edit.draft.content : file.content;

  const toggle = bar.toggle;

  const goForm = (to: DocumentForm) => {
    // Entering the source form on a writable file is entering the edit. The
    // editor is live from there on, exactly as the Files tab's is.
    if (to === "source" && !edit.editing) edit.begin();
    setForm(to);
  };

  return (
    // ALWAYS `--full` (2026-08-27, D8). It was phone-only, which left a desktop
    // document as a page scrolling around a 60vh editor scrolling inside it:
    // two scrollers, and the operator hit both ("the page sort of scrolls
    // around it, and then you can scroll within it... multiple nested scroll
    // zones"). Now the bar sticks, the editor ends where the viewport does, and
    // there is one scroller on both viewports.
    <div className="ws-doc ws-doc--full">
      <DocumentBar
        name={baseName(path)}
        path={dirName(path)}
        dirty={edit.dirty}
        onBack={onBack}
        backLabel={backLabel}
        meta={!canEdit ? <Badge><Lock size={12} strokeWidth={1.8} aria-hidden /> read only</Badge> : null}
      >
        {toggle && (
          <Button size="sm" variant="ghost" onClick={() => goForm(toggle.to)}>
            {toggle.to === "source"
              ? <><Pencil aria-hidden /> {toggle.label}</>
              : <><Eye aria-hidden /> {toggle.label}</>}
          </Button>
        )}
        {bar.discard && (
          <Button
            className="ws-docbar__discard"
            size="sm"
            variant="ghost"
            disabled={edit.saving}
            onClick={() => { edit.cancel(); setForm("rendered"); }}
          >
            Discard
          </Button>
        )}
        {bar.save && (
          <Button
            size="sm"
            variant="primary"
            loading={edit.saving}
            disabled={!bar.saveEnabled}
            onClick={() => { void edit.save().catch(() => {}); }}
          >
            Save
          </Button>
        )}
      </DocumentBar>

      <div className="ws-doc__body">
        <Stack gap="sm">
          {!canEdit && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
              {file.truncated
                ? "This file is too large to show in full, so it is read only here."
                : !file.editable
                  ? "This file is not text, so it cannot be edited here."
                  : "This file is read only."}
            </p>
          )}
          {/* A save that failed leaves the document on screen, so it is a line
              beside content and never a region that replaces it. */}
          {saveErr && <Notice tone="error" detail={saveErr}>That didn't save.</Notice>}

          {form === "source" ? (
            <>
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Saving edits the file on disk. It is not committed, so commit it yourself when ready.
              </p>
              <Editor
                value={body}
                onChange={canEdit ? (v) => {
                  // Live, so the first keystroke is what opens the edit.
                  if (!edit.editing) edit.begin();
                  edit.patch({ content: v });
                } : undefined}
                path={path}
                profile="prose"
                editable={canEdit}
                // No height: `.ws-doc--full` sizes it to what is left of the
                // viewport, on both viewports now.
                ariaLabel={`${baseName(path)}, markdown source`}
              />
            </>
          ) : previewKindFor(path) !== "none" ? (
            // The rendered form of a picture is the picture. Same rule as the
            // markdown case above it (section 4: a file with two forms opens on
            // the rendered one), and it is why an image in `documents` no longer
            // opens as an empty document under a line explaining that it is not
            // text. An SVG is text as well, so it keeps the Edit toggle and its
            // source is one tap away.
            <FilePreview workspace="documents" path={path} name={baseName(path)} takeover={takeover} />
          ) : body.trim() ? (
            <Markdown>{body}</Markdown>
          ) : (
            <p className="ws-muted" style={{ margin: 0 }}>This document is empty.</p>
          )}
        </Stack>
      </div>
    </div>
  );
}
