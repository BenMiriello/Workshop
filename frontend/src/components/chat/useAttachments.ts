import { useCallback, useRef, useState } from "react";
import { chatApi } from "../../lib/chat";
import {
  admit, rejectionNotice, sendableRefs,
  type Attachment, type AttachmentRef,
} from "./attachments";

// THE COMPOSER'S ATTACHED FILES, and the three ways one arrives: the button,
// a drop, a paste. All three call `add`, so a screenshot pasted from the
// clipboard is judged and uploaded by exactly the same path as one dragged in
// from a file manager.
//
// UPLOAD HAPPENS ON ARRIVAL, not on send. The chip appears immediately in an
// `uploading` state and becomes `ready` when the server has the bytes, so the
// wait is spent while the operator is still typing rather than after they press
// Send. It also means a file that is going to be refused is refused now, when
// there is still something to do about it.
//
// A file is NEVER put into the draft text. The operator asked to delete an
// attachment in one action rather than a character at a time, and that is only
// possible while the file is a separate object beside the text.

export type AttachmentsState = {
  items: Attachment[];
  /** What was refused and why. Cleared by the next add or by dismissing it. */
  notice: string | null;
  dismissNotice: () => void;
  /** Judge these files, then upload the ones that pass. */
  add: (files: File[]) => void;
  remove: (localId: string) => void;
  /** Everything the next turn should carry, then clear. Called by send. */
  take: () => AttachmentRef[];
  clear: () => void;
  /** True while a drag carrying files is over the chat. */
  dragging: boolean;
  setDragging: (v: boolean) => void;
};

let seq = 0;
const nextLocalId = () => `att-${Date.now().toString(36)}-${(seq += 1)}`;

export function useAttachments(
  { scope, threadId }: { scope: string; threadId: string | null },
): AttachmentsState {
  const [items, setItems] = useState<Attachment[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  // `admit` needs the CURRENT list, and two files dropped in one gesture are
  // added in one call — but a paste landing while an upload is in flight is a
  // second call against state the first has not committed yet. The ref is what
  // makes the count and duplicate checks see the real list either way.
  const itemsRef = useRef<Attachment[]>([]);
  itemsRef.current = items;

  const add = useCallback((files: File[]) => {
    if (!files.length) return;
    const { accepted, rejected } = admit(itemsRef.current, files);
    setNotice(rejectionNotice(rejected));

    const pending: Attachment[] = accepted.map(({ file, kind }) => ({
      localId: nextLocalId(),
      name: file.name || (kind === "image" ? "pasted-image.png" : "file"),
      size: file.size,
      kind,
      status: "uploading",
    }));
    if (!pending.length) return;
    itemsRef.current = [...itemsRef.current, ...pending];
    setItems(itemsRef.current);

    accepted.forEach(({ file }, i) => {
      const localId = pending[i].localId;
      const name = pending[i].name;
      chatApi.uploadAttachment(file, { scope, threadId })
        .then((rec) => {
          setItems((prev) => prev.map((a) => (
            a.localId === localId
              ? { ...a, id: rec.id, name: rec.name || name, size: rec.size, status: "ready" as const }
              : a
          )));
        })
        .catch((e: unknown) => {
          setItems((prev) => prev.map((a) => (
            a.localId === localId
              ? { ...a, status: "failed" as const, error: e instanceof Error ? e.message : "upload failed" }
              : a
          )));
        });
    });
  }, [scope, threadId]);

  const remove = useCallback((localId: string) => {
    setItems((prev) => prev.filter((a) => a.localId !== localId));
    setNotice(null);
  }, []);

  const clear = useCallback(() => {
    itemsRef.current = [];
    setItems([]);
    setNotice(null);
    setDragging(false);
  }, []);

  // Read off the ref, not off `items`: send is called from a callback that may
  // have closed over an older render, and the turn must carry what is attached
  // NOW. Clearing here rather than in the caller keeps "sent" and "cleared" from
  // ever being two separate decisions that can disagree.
  const take = useCallback((): AttachmentRef[] => {
    const refs = sendableRefs(itemsRef.current);
    clear();
    return refs;
  }, [clear]);

  return {
    items, notice, dismissNotice: () => setNotice(null),
    add, remove, take, clear, dragging, setDragging,
  };
}

/** Files carried by a drop or a paste, deduped by the browser's own list. */
export function filesFromTransfer(dt: DataTransfer | null): File[] {
  if (!dt) return [];
  if (dt.files && dt.files.length) return Array.from(dt.files);
  // A clipboard image arrives as an ITEM with no entry in `files` on some
  // browsers, which is why this is not just `Array.from(dt.files)`.
  const out: File[] = [];
  for (const item of Array.from(dt.items || [])) {
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (f) out.push(f);
  }
  return out;
}

/** Does this drag actually carry files? A drag of selected TEXT also fires
 *  dragover, and lighting the whole chat up for it is noise. */
export function dragHasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types || []).includes("Files");
}
