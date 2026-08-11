import { useEffect, useState } from "react";
import { Modal, Button, Field, Input } from "@workshop/ui";

// A chat names itself once it has enough to go on (chat_naming.generate_title,
// server-side) — this is the operator overriding that. Renaming is permanent
// from the auto-namer's point of view: it only ever writes a title while the
// stored one is still blank, so a name set here can't be clobbered back.
export default function RenameChatDialog({
  open,
  initialTitle,
  busy,
  onCancel,
  onSave,
}: {
  open: boolean;
  initialTitle: string;
  busy: boolean;
  onCancel: () => void;
  onSave: (title: string) => void;
}) {
  const [title, setTitle] = useState(initialTitle);
  // Re-seed from the current title each time the dialog opens, not on every
  // prop change — otherwise a rename in flight would see its own draft
  // overwritten if the underlying thread title changed for any other reason.
  useEffect(() => {
    if (open) setTitle(initialTitle);
  }, [open, initialTitle]);

  const save = () => {
    const trimmed = title.trim();
    if (trimmed) onSave(trimmed);
  };

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="Rename chat"
      actions={
        <>
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!title.trim()} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Title">
        <Input
          data-autofocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); save(); }
          }}
          maxLength={80}
          placeholder="Name this chat…"
        />
      </Field>
    </Modal>
  );
}
