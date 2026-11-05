// "Model…" from the dictation bar's menu: the same pick "chat model …" makes
// by voice, typed. The closest model by name is set; the bar offers Cancel.

import { useState } from "react";
import { Button, Input, Modal, Notice, Stack } from "@workshop/ui";

export function ModelAsk({ open, onClose, onPick }: {
  open: boolean;
  onClose: () => void;
  /** False when nothing matched. */
  onPick: (query: string) => Promise<boolean>;
}) {
  const [q, setQ] = useState("");
  const [missed, setMissed] = useState(false);
  const submit = async () => {
    if (!q.trim()) return;
    if (await onPick(q.trim())) { setQ(""); onClose(); }
    else setMissed(true);
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Change the model"
      actions={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => void submit()}>Change</Button>
        </>
      )}
    >
      <Stack gap="sm">
        <Input
          autoFocus
          value={q}
          onChange={(e) => { setQ(e.target.value); setMissed(false); }}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          placeholder="Opus, Sonnet 5.5…"
          aria-label="Model name"
          data-dictation="off"
        />
        {missed && <Notice tone="warn">No model like that in this chat.</Notice>}
      </Stack>
    </Modal>
  );
}
