import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { PageHeader, Button, Modal, Field, Input } from "../../../ui";
import { notesApi, type NoteMeta, type Note } from "./api";
import { makeResolver } from "./wikilinks";
import NoteSidebar from "./NoteSidebar";
import NoteView from "./NoteView";

export default function Notes() {
  const [params, setParams] = useSearchParams();
  const selectedPath = params.get("path");

  const [notes, setNotes] = useState<NoteMeta[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);

  const [note, setNote] = useState<Note | null>(null);
  const [noteLoading, setNoteLoading] = useState(false);
  const [noteErr, setNoteErr] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [createBusy, setCreateBusy] = useState(false);

  const resolver = useMemo(() => makeResolver(notes ?? []), [notes]);

  const loadList = useCallback(() => {
    setListErr(null);
    return notesApi
      .list()
      .then((r) => setNotes(r.notes))
      .catch((e) => {
        setNotes([]);
        setListErr(String(e.message || e));
      });
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  // Load the selected note whenever the ?path= changes.
  const [reloadTick, setReloadTick] = useState(0);
  useEffect(() => {
    if (!selectedPath) {
      setNote(null);
      setNoteErr(null);
      return;
    }
    let live = true;
    setNoteLoading(true);
    setNoteErr(null);
    notesApi
      .read(selectedPath)
      .then((n) => live && setNote(n))
      .catch((e) => live && setNoteErr(String(e.message || e)))
      .finally(() => live && setNoteLoading(false));
    return () => {
      live = false;
    };
  }, [selectedPath, reloadTick]);

  const select = useCallback(
    (path: string) => {
      setParams(path ? { path } : {}, { replace: false });
    },
    [setParams],
  );

  const openCreate = (prefill: string) => {
    setNewPath(prefill.replace(/\.md$/i, ""));
    setCreateErr(null);
    setCreating(true);
  };

  const doCreate = async () => {
    setCreateBusy(true);
    setCreateErr(null);
    try {
      const r = await notesApi.create(newPath.trim(), `# ${newPath.trim().split("/").pop()}\n\n`);
      setCreating(false);
      await loadList();
      select(r.path);
    } catch (e: unknown) {
      setCreateErr(String((e as Error).message || e));
    } finally {
      setCreateBusy(false);
    }
  };

  // After a write/rename/delete: refresh the list, then reselect or clear.
  const onMutated = useCallback(
    async (path: string | null) => {
      await loadList();
      if (path === null) {
        select("");
      } else if (path === selectedPath) {
        setReloadTick((t) => t + 1); // same path, force reload (edit saved)
      } else {
        select(path); // moved (rename) — follow it
      }
    },
    [loadList, select, selectedPath],
  );

  const count = notes?.length ?? 0;
  const sub = notes === null ? "Loading…" : `${count} note${count === 1 ? "" : "s"} in your vault`;

  return (
    <div>
      <PageHeader
        title="Notes"
        sub={sub}
        actions={
          <Button variant="primary" onClick={() => openCreate("")}>
            <Plus size={16} /> New note
          </Button>
        }
      />

      <div className="ws-note-layout">
        <NoteSidebar
          notes={notes}
          error={listErr}
          onRetry={loadList}
          activePath={selectedPath}
          onSelect={select}
        />
        <div className="ws-note-main">
          <NoteView
            key={selectedPath ?? "none"}
            note={note}
            loading={noteLoading}
            error={noteErr}
            onRetry={() => setReloadTick((t) => t + 1)}
            resolver={resolver}
            onOpen={select}
            onCreate={openCreate}
            onMutated={onMutated}
          />
        </div>
      </div>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New note"
        actions={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={createBusy} onClick={doCreate}>
              Create
            </Button>
          </>
        }
      >
        <Field
          label="Path"
          help="Folders are created as needed. The .md extension is added for you."
          error={createErr || undefined}
        >
          <Input
            value={newPath}
            autoFocus
            placeholder="inbox/my-first-note"
            onChange={(e) => setNewPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doCreate()}
          />
        </Field>
      </Modal>
    </div>
  );
}
