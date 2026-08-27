// Who else can find this conversation, as a dialog on the chat's own toolbar.
//
// WHERE IT WAS AND WHY IT MOVED. This control used to be a badge floating in
// the corner of a chat card on the Memory page's Chats tab. Two things were
// wrong with that. The tab itself was one of four unrelated collections behind
// one switch on a page named after a different one, and it went on 2026-08-27
// (memory-page-and-file-viewer-2026-08-27.md D1). And a per-chat setting drawn
// as an overlay on a card in a list was already the second attempt at placing
// it: the first put a full "Other chats find it: <select>" row inside the grid
// cell, where it fought the card for width and repeated the same sentence a
// hundred times down the page.
//
// The chat's own toolbar is the answer to both. It is a property OF the
// conversation, read while you are in the conversation, filed with Archive and
// the rest of what you can do to a chat.
//
// CURATION, NOT SECURITY. This changes what other chats' searches turn up. It
// does not restrict what anything can read: everything here runs as one uid.
// Per SEC-A2, do not relabel this permission or access.
import { useCallback, useState } from "react";
import { Button, Field, Modal, Select, Stack } from "@workshop/ui";
import {
  chatItemRef,
  effectiveShare,
  setItem as setItemShare,
  SHARE_HELP,
  SHARE_LABEL,
  SHARE_VALUES,
  type MemoryPolicy,
  type ShareValue,
} from "../../lib/memoryPolicy";

/** Explicit Save rather than commit-on-change, per frontend/CLAUDE.md rule 7:
 *  this writes config. */
export function ChatShareDialog({
  threadId, scope, title, policy, onSaved, onClose,
}: {
  threadId: string;
  /** The chat's scope. Read so a chat with no setting of its own resolves to
   *  its scope's default rather than skipping straight to the global one. */
  scope: string;
  title: string;
  /** The loaded policy, or null while it is still loading or failed to load.
   *  Null resolves to the stored default, which is what the backend would
   *  apply anyway, so the dialog is usable either way. */
  policy: MemoryPolicy | null;
  onSaved: (p: MemoryPolicy) => void;
  onClose: () => void;
}) {
  const ref = chatItemRef(threadId);
  const stored = effectiveShare(policy, "chat", scope, ref);
  const [value, setValue] = useState<ShareValue>(stored);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = value !== stored;

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      onSaved(await setItemShare(ref, value));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }, [ref, value, onSaved]);

  return (
    <Modal
      open
      onClose={onClose}
      title={`Sharing: ${title || "Chat"}`}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!dirty} loading={saving} onClick={() => { void save(); }}>
            Save
          </Button>
        </>
      }
    >
      <Stack gap="lg">
        <Field label="Other chats find it" htmlFor="chat-share-select" help={SHARE_HELP[value]}>
          <Select
            id="chat-share-select"
            value={value}
            onChange={(e) => setValue(e.target.value as ShareValue)}
            data-autofocus
          >
            {SHARE_VALUES.map((v) => (
              <option key={v} value={v}>{SHARE_LABEL[v]}</option>
            ))}
          </Select>
        </Field>
        {err && <p className="ws-field__error">{err}</p>}
      </Stack>
    </Modal>
  );
}
