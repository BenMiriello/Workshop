// The tag row, the history sheet, the save line and the notice of another
// writer's overlapping change, for a VersionedDocument.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { RotateCcw, X } from "lucide-react";
import { Button, Input } from "./controls";
import { Row, SectionLabel, Stack } from "./layout";
import { Notice, Skeleton, StateBlock } from "./feedback";
import { Sheet } from "./Sheet";
import { timeAbsolute, timeAgo } from "./time";
import { messageOf } from "./httpError";
import type { DocumentDraft, DocumentSource, DocumentState, DocumentVersionInfo } from "./documentSource";

/** The tag row in edit mode: chips that come off always, and the field that
 *  adds only while `adding` (the bar's tag button opens it). */
export function TagEditor({
  tags, adding, onChange, options,
}: {
  tags: string[];
  adding: boolean;
  onChange: (next: string[]) => void;
  options?: (q: string) => string[] | Promise<string[]>;
}) {
  const [text, setText] = useState("");
  const [hints, setHints] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    const q = text.trim();
    if (!adding || !options || !q) { setHints([]); return; }
    Promise.resolve(options(q)).then((r) => {
      if (live) setHints(r.filter((h) => !tags.includes(h)).slice(0, 6));
    }, () => { if (live) setHints([]); });
    return () => { live = false; };
  }, [text, options, tags, adding]);

  const add = (raw: string) => {
    const t = raw.trim().replace(/^@/, "");
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setText("");
  };

  if (!adding && tags.length === 0) return null;
  return (
    <Stack gap="sm">
      <Row wrap className="ws-vdoc__chips">
        {tags.map((t) => (
          <span key={t} className="ws-vdoc__chip">
            <span className="ws-vdoc__chiptext">{t}</span>
            <button type="button" className="ws-vdoc__chipx" aria-label={`Remove tag ${t}`}
              onClick={() => onChange(tags.filter((x) => x !== t))}>
              <X aria-hidden size={14} />
            </button>
          </span>
        ))}
        {adding && <Input
          className="ws-vdoc__tagin"
          autoFocus
          value={text}
          placeholder={tags.length ? "Add tag" : "Add a tag"}
          aria-label="Add a tag"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); }
            if (e.key === "Backspace" && !text && tags.length) onChange(tags.slice(0, -1));
          }}
          onBlur={() => { if (text.trim()) add(text); }}
        />}
      </Row>
      {adding && hints.length > 0 && (
        <Row wrap className="ws-vdoc__hints">
          {hints.map((h) => (
            <Button key={h} size="sm" variant="ghost" onMouseDown={(e) => e.preventDefault()}
              onClick={() => add(h)}>{h}</Button>
          ))}
        </Row>
      )}
    </Stack>
  );
}

export function HistorySheet({
  open, onClose, source, current, onRestore, read, shownTitle,
}: {
  open: boolean;
  onClose: () => void;
  source: DocumentSource;
  current: number;
  onRestore: (version: number) => Promise<void>;
  /** The document's reading form, and how a title reads, as the frame draws them. */
  read: (body: string) => ReactNode;
  shownTitle: (title: string) => string;
}) {
  const [list, setList] = useState<DocumentVersionInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<DocumentVersionInfo | null>(null);
  const [preview, setPreview] = useState<DocumentDraft | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setList(null); setErr(null); setPicked(null); setPreview(null);
    source.versions().then((v) => { if (live) setList(v); }, (e) => { if (live) setErr(messageOf(e)); });
    return () => { live = false; };
  }, [open, source]);

  useEffect(() => {
    if (picked == null) return;
    let live = true;
    setPreview(null);
    source.version(picked.version).then((d) => { if (live) setPreview(d); }, (e) => { if (live) setErr(messageOf(e)); });
    return () => { live = false; };
  }, [picked, source]);

  const restore = async () => {
    if (picked == null) return;
    setBusy(true);
    try {
      await onRestore(picked.version);
      onClose();
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  const where = (v: DocumentVersionInfo) => `${timeAbsolute(v.savedAt)}${v.savedFrom ? ` · ${v.savedFrom}` : ""}`;

  return (
    <Sheet open={open} onClose={onClose} title={picked == null ? "History" : timeAbsolute(picked.savedAt)}
      actions={picked != null ? (
        <Row>
          <Button variant="ghost" onClick={() => setPicked(null)}>All versions</Button>
          {picked.version === current ? (
            <span className="ws-vdoc__vmeta">This is the current text</span>
          ) : (
            <Button variant="primary" loading={busy} onClick={restore}>
              <RotateCcw aria-hidden size={16} /> Restore this version
            </Button>
          )}
        </Row>
      ) : undefined}>
      {err && <Notice tone="error" detail={err}>History could not be read.</Notice>}
      {picked == null ? (
        list == null && !err ? (
          <Stack gap="sm"><Skeleton width="70%" /><Skeleton width="60%" /><Skeleton width="65%" /></Stack>
        ) : list && list.length === 0 ? (
          <StateBlock title="No versions yet" />
        ) : (
          <ul className="ws-vdoc__versions">
            {(list ?? []).map((v) => (
              <li key={v.version}>
                <button type="button" className="ws-vdoc__version" onClick={() => setPicked(v)}>
                  <span className="ws-vdoc__vtitle">{shownTitle(v.title) || "Untitled"}</span>
                  <span className="ws-vdoc__vmeta">{where(v)}{v.version === current ? " · current" : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : preview == null ? (
        <Stack gap="sm"><Skeleton width="50%" /><Skeleton width="90%" /><Skeleton width="80%" /></Stack>
      ) : (
        <Stack>
          <strong>{shownTitle(preview.title) || "Untitled"}</strong>
          {read(preview.body)}
        </Stack>
      )}
    </Sheet>
  );
}

/** Another writer changed the same place: theirs to look at, use, or set aside. */
export function OverlapNotice({ theirs, title, read, onUse, onKeep }: {
  theirs: DocumentState;
  title: string;
  read: (text: string) => ReactNode;
  onUse: () => void;
  onKeep: () => void;
}) {
  const [show, setShow] = useState(false);
  const when = theirs.updatedAt
    ? `Theirs: ${timeAbsolute(theirs.updatedAt)}${theirs.savedFrom ? `, ${theirs.savedFrom}` : ""}.` : undefined;
  return (
    <>
      <Notice tone="warn" detail={when}
        action={(
          <Row wrap>
            <Button size="sm" variant="ghost" onClick={() => setShow((v) => !v)}>{show ? "Hide theirs" : "Show theirs"}</Button>
            <Button size="sm" variant="ghost" onClick={onUse}>Use theirs</Button>
            <Button size="sm" variant="ghost" onClick={onKeep}>Keep mine</Button>
          </Row>
        )}>
        Also changed elsewhere in the same place. Yours is kept there.
      </Notice>
      {show && (
        <div className="ws-vdoc__theirs">
          <SectionLabel>Their version</SectionLabel>
          <strong>{title || "Untitled"}</strong>
          {read(theirs.body)}
        </div>
      )}
    </>
  );
}

/** What the bar says about saving: a failure, a save in flight, "Saved" for a
 *  moment after one lands, then when and where it was last saved. */
export function useSaveLine(state: DocumentState | null, status: string, saveErr: string | null,
  savedAt: number | null): ReactNode {
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!savedAt) return;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 1800);
    return () => clearTimeout(t);
  }, [savedAt]);
  return useMemo(() => {
    if (!state) return undefined;
    if (status === "failed") return <span title={saveErr ?? undefined}>Not saved yet, retrying</span>;
    if (status === "saving") return "Saving…";
    if (flash) return "Saved";
    if (state.updatedAt) return `Saved ${timeAgo(state.updatedAt)}${state.savedFrom ? ` from ${state.savedFrom}` : ""}`;
    return undefined;
  }, [state, flash, status, saveErr]);
}
