import { useRef } from "react";
import { AlertTriangle, FileText, Image as ImageIcon, Paperclip, X } from "lucide-react";
import { chatApi } from "../../lib/chat";
import { formatSize, type Attachment, type AttachmentKind } from "./attachments";

// A chip is ONE object. The whole point of rendering an attachment beside the
// draft rather than as text inside it is that removing it is a single click,
// not a run of backspaces.
//
// The remove control sits on the LEFT of the chip and appears on hover or focus
// (it is always present for a keyboard, just not always painted). On touch there
// is no hover, so `:focus-within` and the coarse-pointer rule in the stylesheet
// keep it visible; a control that only exists under a mouse is not a control.

function KindIcon({ kind }: { kind: AttachmentKind }) {
  if (kind === "image") return <ImageIcon size={14} strokeWidth={1.8} aria-hidden />;
  if (kind === "pdf") return <FileText size={14} strokeWidth={1.8} aria-hidden />;
  return <FileText size={14} strokeWidth={1.8} aria-hidden />;
}

function Chip({ att, onRemove }: { att: Attachment; onRemove: () => void }) {
  const failed = att.status === "failed";
  const uploading = att.status === "uploading";
  // A thumbnail only once the bytes are on the server: there is nothing to point
  // at before that, and an object URL for the local File would have to be
  // revoked on unmount to avoid leaking one per drop.
  const thumb = att.kind === "image" && att.status === "ready" && att.id
    ? chatApi.attachmentUrl(att.id)
    : null;

  return (
    <span
      className={`chat-attach${failed ? " is-failed" : ""}${uploading ? " is-uploading" : ""}`}
      title={failed ? (att.error || "Upload failed") : `${att.name} (${formatSize(att.size)})`}
    >
      <button
        type="button"
        className="chat-attach__remove"
        onClick={onRemove}
        aria-label={`Remove ${att.name}`}
      >
        <X size={12} strokeWidth={2.2} aria-hidden />
      </button>
      <span className="chat-attach__mark" aria-hidden>
        {thumb
          ? <img className="chat-attach__thumb" src={thumb} alt="" />
          : failed
            ? <AlertTriangle size={14} strokeWidth={1.8} />
            : <KindIcon kind={att.kind} />}
      </span>
      {/* The filename, not the path: a path tells the operator nothing they did
          not already know and pushes the name they DO need off the chip. */}
      <span className="chat-attach__name">{att.name}</span>
      <span className="chat-attach__meta">
        {failed ? "failed" : uploading ? "uploading" : formatSize(att.size)}
      </span>
    </span>
  );
}

/** The composer's row of attached files. Renders nothing when empty, so an
 *  ordinary chat is the same height it always was. */
export default function AttachmentChips({
  items, notice, onRemove, onDismissNotice,
}: {
  items: Attachment[];
  notice?: string | null;
  onRemove: (localId: string) => void;
  onDismissNotice?: () => void;
}) {
  if (!items.length && !notice) return null;
  return (
    <div className="chat-attach-row">
      {items.map((a) => (
        <Chip key={a.localId} att={a} onRemove={() => onRemove(a.localId)} />
      ))}
      {notice && (
        <button
          type="button"
          className="chat-attach-notice"
          onClick={onDismissNotice}
          title="Dismiss"
        >
          <AlertTriangle size={13} strokeWidth={1.8} aria-hidden />
          <span>{notice}</span>
        </button>
      )}
    </div>
  );
}

/** The one control that opens the file dialog. A single button rather than one
 *  per kind: the operator asked for one, and the picker already lets the OS do
 *  the filtering.
 *
 *  A real <button> driving a hidden <input>, not a <label> wrapping one: a label
 *  is not in the tab order and screen readers announce it as text rather than as
 *  something you can press. */
export function AttachButton({ onFiles }: { onFiles: (files: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="chat-slash-btn"
        onClick={() => inputRef.current?.click()}
        aria-label="Attach images or files"
        title="Attach images or files"
      >
        <Paperclip size={16} aria-hidden />
      </button>
      <input
        ref={inputRef}
        className="chat-attach-input"
        type="file"
        multiple
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          onFiles(Array.from(e.target.files || []));
          // Reset, or picking the SAME file twice in a row fires no change event
          // and the second attach silently does nothing.
          e.target.value = "";
        }}
      />
    </>
  );
}
