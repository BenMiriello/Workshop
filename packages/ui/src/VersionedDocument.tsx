// VersionedDocument: one markdown document with a title and tags that saves as
// it is typed, keeps a history to restore from, and merges what another device
// or app saved meanwhile instead of overwriting it or being overwritten.
//
// It exists so two apps can show and edit the SAME documents without building
// the frame twice. Where the document lives is the host's business, reached
// through `DocumentSource`; what the host adds inside the text (suggestions,
// marked ranges, what to do with a selection) is plain data passed to the
// Editor. Nothing here knows which app it is in.
//
// There is one text, drawn by one surface: the page-look editor, read-only
// while it is read and writable once it is tapped or Edit is pressed, so
// starting to write changes nothing on screen but the caret. What a host adds
// to the drawing (its links, mentions, words shown as others) is `marks`, drawn
// the same way in both. Saving is not a step anyone takes (`useDocumentSync`).
// History is the source's: it keeps one entry per stretch of typing on one
// device, and restoring an entry makes it current as a new one, so restoring
// is itself undoable.
import { useCallback, useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Check, History, MoreHorizontal, Pencil, Tag as TagIcon } from "lucide-react";
import { Button, Tag } from "./controls";
import { Row, Stack } from "./layout";
import { Skeleton, StateBlock } from "./feedback";
import { DocumentBar } from "./DocumentBar";
import { Menu, MenuItem } from "./Menu";
import { Editor } from "./EditorCore";
import { DocumentText } from "./DocumentText";
import type { EditorMarks } from "./editorMarks";
import { catFor } from "./status";
import { cx } from "./cx";
import { ReadAloudButton } from "./ReadAloudButton";
import { HistorySheet, OverlapNotice, TagEditor, useSaveLine } from "./DocumentParts";
import { useDocumentSync } from "./useDocumentSync";
import type { DocumentDraft, DocumentSource, DocumentState } from "./documentSource";
import type { EditorCompletionSource, EditorHighlight, EditorSelection } from "./editorAssist";
import type { EditorConceal } from "./editorConceal";

export type {
  DocumentDraft, DocumentSaveResult, DocumentSource, DocumentState, DocumentVersionInfo, DocumentWriteMeta,
} from "./documentSource";

export interface VersionedDocumentProps {
  source: DocumentSource;
  /** Identifies the document. A new key reloads everything. */
  docKey: string;
  /** Drawn ahead of the title in the header, e.g. the folder. */
  path?: string;
  /** Open straight into the editor (a document that was just created). */
  startEditing?: boolean;
  /** Fill the content area with one scroller, as a full page does. */
  fill?: boolean;
  /** Fill a box the host has already sized: the bar stays above the body and
   *  the body scrolls beneath it. */
  fit?: boolean;
  completions?: EditorCompletionSource[];
  highlights?: EditorHighlight[];
  onSelectionChange?: (sel: EditorSelection | null) => void;
  /** Suggestions while adding a tag. */
  tagOptions?: (query: string) => string[] | Promise<string[]>;
  /** @deprecated Not drawn. The text is always drawn by the editor, in reading
   *  and in writing alike; pass what the host adds to it as `marks`, and handle
   *  its links with `onLink`. Removed in the next breaking release. */
  renderBody?: (body: string) => ReactNode;
  /** The host's additions to how the text is drawn: mentions, words shown as
   *  others, ranges to mark (editorMarks). The same while reading and writing. */
  marks?: EditorMarks;
  /** A link in the text was followed (editorMarks). */
  onLink?: (target: string) => void;
  /** Scrolls the text to `pos` whenever `key` changes. */
  scrollTo?: { pos: number; key: number } | null;
  /** How a title reads wherever it is not being edited. A title typed in
   *  since the document opened reads as typed. */
  titleDisplay?: (title: string) => string;
  /** Ranges of the text blurred while editing, except near the caret. */
  conceal?: EditorConceal | null;
  /** Under the body in both forms: backlinks, related items, the host's panel. */
  aside?: ReactNode;
  /** Extra header controls, beside the frame's own. */
  actions?: ReactNode;
  /** Extra `MenuItem`s for the bar's More menu, ahead of the frame's own: the
   *  host's less frequent actions, so the title keeps its room on a phone. */
  menuItems?: ReactNode;
  /** A save or a restore landed. */
  onSaved?: (state: DocumentState) => void;
  /** The text changed, as typed or as merged, for a host that marks the live text. */
  onDraftChange?: (draft: DocumentDraft) => void;
  onBack?: () => void;
  backLabel?: string;
  /** Typing pause before the text is saved, in ms. */
  autosaveMs?: number;
  /** How often an open document checks for a newer save, in ms. */
  pollMs?: number;
  placeholder?: string;
}

export function VersionedDocument({
  source, docKey, path, startEditing = false, fill = false, fit = false,
  completions, highlights, onSelectionChange, tagOptions, marks, onLink, scrollTo, titleDisplay, conceal,
  aside, actions, menuItems, onSaved, onDraftChange, onBack, backLabel,
  autosaveMs = 1000, pollMs = 10000, placeholder = "",
}: VersionedDocumentProps) {
  const [editing, setEditing] = useState(false);
  const [tagsOpen, setTagsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [titleTyped, setTitleTyped] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const editorSel = useRef<string | null>(null);

  const onOpened = useCallback((s: DocumentState, d: DocumentDraft) => {
    setTagsOpen(false);
    setTitleTyped(false);
    // An empty document is one about to be written: it opens for writing.
    setEditing(!s.readOnly && (startEditing || (!d.title.trim() && !d.body.trim())));
  }, [startEditing]);

  const sync = useDocumentSync({ source, docKey, autosaveMs, pollMs, onSaved, onOpened });
  const { state, draft, setDraft, overlap, flush } = sync;

  useEffect(() => { onDraftChange?.(draft); }, [draft, onDraftChange]);

  const done = useCallback(() => {
    setEditing(false); setTagsOpen(false);
    void flush();
  }, [flush]);

  // Ctrl/Cmd+S saves at once, as every editor does.
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void flush();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, flush]);

  const meta = useSaveLine(state, sync.status, sync.saveErr, sync.savedAt);

  // An empty document is a blank page: the editor is one line tall, so a click
  // anywhere under it starts writing, the way it does on paper.
  const blank = !!state && !state.readOnly && !draft.body.trim();
  const writeOnBlank = useCallback((e: MouseEvent) => {
    if ((e.target as Element | null)?.closest("button, a, input, textarea, .cm-content")) return;
    setEditing(true);
    requestAnimationFrame(() => rootRef.current?.querySelector<HTMLElement>(".ws-doc__body .cm-content")?.focus());
  }, []);

  if (sync.loadErr) {
    return (
      <StateBlock variant="error" title="This document could not be opened" hint={sync.loadErr}
        action={<Button onClick={sync.retry}>Try again</Button>} />
    );
  }
  if (!state) {
    return (
      <Stack gap="sm">
        <Skeleton width="40%" /><Skeleton width="90%" /><Skeleton width="85%" /><Skeleton width="60%" />
      </Stack>
    );
  }

  const read = (text: string) => <DocumentText text={text} marks={marks} onLink={onLink} />;
  const shownTitle = (t: string) => (titleDisplay ? titleDisplay(t) : t);
  const writable = !state.readOnly;

  return (
    <div ref={rootRef} className={cx("ws-doc", "ws-vdoc", fill && "ws-doc--full", fit && "ws-doc--fit",
      !state.readOnly && "ws-vdoc--writable")}>
      <DocumentBar
        variant="title"
        name={draft.title}
        nameShown={titleDisplay && !titleTyped ? titleDisplay(draft.title) : undefined}
        namePlaceholder="Untitled"
        onNameChange={editing ? (t) => { setTitleTyped(true); setDraft((d) => ({ ...d, title: t })); } : undefined}
        path={path}
        dirty={sync.pending}
        quietDirty
        meta={meta}
        onBack={onBack}
        backLabel={backLabel}
      >
        {actions}
        <ReadAloudButton source={`document:${docKey}`} within={rootRef}
          selected={editing ? () => editorSel.current : undefined}
          text={() => [draft.title, draft.body].filter((x) => x.trim()).join("\n\n")} />
        <Menu label="More" trigger={<Button variant="ghost" icon aria-label="More" title="More"><MoreHorizontal aria-hidden size={16} /></Button>}>
          {menuItems}
          {!state.readOnly && (
            <MenuItem icon={<TagIcon aria-hidden size={16} />} onSelect={() => {
              if (editing) { setTagsOpen((v) => !v); return; }
              setEditing(true); setTagsOpen(true);
            }}>
              Tags
            </MenuItem>
          )}
          <MenuItem icon={<History aria-hidden size={16} />} onSelect={() => setHistoryOpen(true)}>History</MenuItem>
        </Menu>
        {editing ? (
          <Button variant="ghost" icon aria-label="Done" title="Done" onClick={done}>
            <Check aria-hidden size={18} />
          </Button>
        ) : !state.readOnly && (
          <Button variant="ghost" icon aria-label="Edit" title="Edit"
            onClick={() => setEditing(true)}>
            <Pencil aria-hidden size={16} />
          </Button>
        )}
      </DocumentBar>

      <div className="ws-doc__body" onClick={blank ? writeOnBlank : undefined}>
        <Stack>
          {overlap && (
            <OverlapNotice theirs={overlap.theirs} title={shownTitle(overlap.theirs.title)}
              read={read} onUse={() => void sync.restore(overlap.theirs.version)} onKeep={sync.clearOverlap} />
          )}

          {editing ? (
            <TagEditor tags={draft.tags} options={tagOptions} adding={tagsOpen}
              onChange={(tags) => setDraft((d) => ({ ...d, tags }))} />
          ) : draft.tags.length > 0 && (
            <Row wrap className="ws-vdoc__chips">
              {draft.tags.map((t) => <Tag key={t} hue={catFor(t)} variant="neutral">{t}</Tag>)}
            </Row>
          )}

          <Editor
            value={draft.body}
            onChange={(b) => setDraft((d) => ({ ...d, body: b }))}
            editable={editing && writable}
            onStartEditing={writable ? () => setEditing(true) : undefined}
            path="document.md"
            profile="prose"
            placeholder={placeholder}
            ariaLabel={`Text of ${draft.title || "this document"}`}
            completions={completions}
            highlights={highlights}
            conceal={editing ? conceal : null}
            marks={marks}
            onLink={onLink}
            scrollTo={scrollTo}
            look="page"
            onSelectionChange={(sel) => { editorSel.current = sel?.text ?? null; onSelectionChange?.(sel); }}
          />
          {aside}
        </Stack>
      </div>

      <HistorySheet open={historyOpen} onClose={() => setHistoryOpen(false)} source={source}
        current={state.version} onRestore={sync.restore} read={read} shownTitle={shownTitle} />
    </div>
  );
}
