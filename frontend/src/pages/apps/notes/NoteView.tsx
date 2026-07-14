import { useEffect, useState } from "react";
import { FileText, Pencil, Trash2, TextCursorInput, Link2 } from "lucide-react";
import {
  Button,
  Row,
  Stack,
  SectionLabel,
  Textarea,
  Badge,
  Field,
  Input,
  Modal,
  ConfirmDialog,
  StateBlock,
  Skeleton,
  ActionBar,
  Markdown,
  useEditMode,
} from "../../../ui";
import { notesApi, type Note, type Backlink } from "./api";
import { transformForPreview, parseWikiHref, type Resolver } from "./wikilinks";

function fmtTime(epoch: number): string {
  try {
    return new Date(epoch * 1000).toLocaleString();
  } catch {
    return "";
  }
}

export default function NoteView({
  note,
  loading,
  error,
  onRetry,
  resolver,
  onOpen,
  onCreate,
  onMutated,
}: {
  note: Note | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  resolver: Resolver;
  onOpen: (path: string) => void;
  onCreate: (prefill: string) => void;
  onMutated: (path: string | null) => void;
}) {
  const edit = useEditMode<{ content: string }>({ content: note?.content ?? "" }, async (draft) => {
    if (!note) return;
    await notesApi.update(note.path, draft.content);
    onMutated(note.path);
  });

  const [renaming, setRenaming] = useState(false);
  const [renameTo, setRenameTo] = useState("");
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [delBusy, setDelBusy] = useState(false);

  // Backlinks for the current note (own fetch — per-note, four states).
  const [backlinks, setBacklinks] = useState<Backlink[] | null>(null);
  const [blError, setBlError] = useState<string | null>(null);
  useEffect(() => {
    if (!note) return;
    let live = true;
    setBacklinks(null);
    setBlError(null);
    notesApi
      .backlinks(note.path)
      .then((r) => live && setBacklinks(r.backlinks))
      .catch((e) => live && setBlError(String(e.message || e)));
    return () => {
      live = false;
    };
  }, [note?.path, note?.modified]);

  if (loading) {
    return (
      <Stack gap="sm">
        <Skeleton width="40%" />
        <Skeleton width="90%" />
        <Skeleton width="80%" />
      </Stack>
    );
  }
  if (error) {
    return (
      <StateBlock
        variant="error"
        title="Could not load note"
        hint={error}
        action={<Button onClick={onRetry}>Retry</Button>}
      />
    );
  }
  if (!note) {
    return (
      <StateBlock
        icon={<FileText size={20} />}
        title="No note selected"
        hint="Pick a note from the list, or create one with New note."
      />
    );
  }

  const startRename = () => {
    setRenameTo(note.path);
    setRenameErr(null);
    setRenaming(true);
  };
  const doRename = async () => {
    setRenameBusy(true);
    setRenameErr(null);
    try {
      const r = await notesApi.rename(note.path, renameTo.trim());
      setRenaming(false);
      onMutated(r.path);
    } catch (e: unknown) {
      setRenameErr(String((e as Error).message || e));
    } finally {
      setRenameBusy(false);
    }
  };
  const doDelete = async () => {
    setDelBusy(true);
    try {
      await notesApi.remove(note.path);
      setConfirmDel(false);
      onMutated(null);
    } finally {
      setDelBusy(false);
    }
  };

  const onPreviewClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a");
    if (!a) return;
    const parsed = parseWikiHref(a.getAttribute("href") || "");
    if (!parsed) return; // ordinary link — leave it
    e.preventDefault();
    if (parsed.kind === "open") onOpen(parsed.value);
    else onCreate(parsed.value);
  };

  return (
    <Stack gap="lg">
      <Row between>
        <Stack gap="sm">
          <h2 className="ws-note-title">{note.title}</h2>
          <span className="ws-note-meta">
            {note.path} · edited {fmtTime(note.modified)}
          </span>
        </Stack>
        {!edit.editing && (
          <Row>
            <Button onClick={edit.begin}>
              <Pencil size={16} /> Edit
            </Button>
            <Button variant="ghost" onClick={startRename}>
              <TextCursorInput size={16} /> Rename
            </Button>
            <Button variant="danger" onClick={() => setConfirmDel(true)}>
              <Trash2 size={16} /> Delete
            </Button>
          </Row>
        )}
      </Row>

      {edit.editing ? (
        <>
          <Textarea
            className="ws-note-editor"
            value={edit.draft.content}
            onChange={(e) => edit.set("content", e.target.value)}
            spellCheck={false}
            aria-label="Note content"
          />
          <ActionBar flash={edit.flash ? "Saved" : undefined}>
            <Button variant="primary" loading={edit.saving} onClick={edit.save}>
              Save
            </Button>
            <Button variant="ghost" onClick={edit.cancel}>
              Cancel
            </Button>
            {edit.dirty && <Badge>unsaved</Badge>}
          </ActionBar>
        </>
      ) : (
        <div onClick={onPreviewClick}>
          <Markdown>{transformForPreview(note.content, resolver) || "_Empty note._"}</Markdown>
        </div>
      )}

      <Stack gap="sm">
        <SectionLabel icon={<Link2 size={14} />}>
          Backlinks{backlinks ? ` (${backlinks.length})` : ""}
        </SectionLabel>
        {blError ? (
          <StateBlock variant="error" title="Could not load backlinks" hint={blError} />
        ) : backlinks === null ? (
          <Skeleton width="60%" />
        ) : backlinks.length === 0 ? (
          <span className="ws-note-meta">No backlinks yet — link here from another note with [[…]].</span>
        ) : (
          <Stack gap="sm">
            {backlinks.map((b, i) => (
              <button key={i} type="button" className="ws-note-backlink" onClick={() => onOpen(b.path)}>
                <span className="ws-note-backlink-path">{b.path}</span>
                <span className="ws-note-meta">{b.context}</span>
              </button>
            ))}
          </Stack>
        )}
      </Stack>

      <Modal
        open={renaming}
        onClose={() => setRenaming(false)}
        title="Rename note"
        actions={
          <>
            <Button variant="ghost" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={renameBusy} onClick={doRename}>
              Rename
            </Button>
          </>
        }
      >
        <Field
          label="New path"
          help="Referring [[wikilinks]] are rewritten to the new path."
          error={renameErr || undefined}
        >
          <Input
            value={renameTo}
            autoFocus
            onChange={(e) => setRenameTo(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doRename()}
          />
        </Field>
      </Modal>

      <ConfirmDialog
        open={confirmDel}
        onCancel={() => setConfirmDel(false)}
        onConfirm={doDelete}
        busy={delBusy}
        title="Delete note"
        body={`Delete "${note.path}"? This removes the file from the vault and cannot be undone.`}
        confirmLabel="Delete"
      />
    </Stack>
  );
}
