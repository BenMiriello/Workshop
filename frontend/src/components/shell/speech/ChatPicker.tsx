// "Choose a chat…" from the dictation bar's menu: any chat, found by title or
// by what was said in it, becomes where the words go, open or not.

import { useEffect, useState } from "react";
import { MessageSquare } from "lucide-react";
import { Button, Input, Modal, Skeleton, StateBlock, Stack } from "@workshop/ui";
import { searchChats, type ChatTarget } from "../../../lib/speech/chatTargets";

const WAIT_MS = 200;

export function ChatPicker({ open, onClose, onPick }: {
  open: boolean;
  onClose: () => void;
  onPick: (t: ChatTarget) => void;
}) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ChatTarget[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tries, setTries] = useState(0);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setFailed(false);
    const t = setTimeout(() => {
      searchChats(q).then(
        (found) => { if (live) setRows(found); },
        () => { if (live) setFailed(true); },
      );
    }, q ? WAIT_MS : 0);
    return () => { live = false; clearTimeout(t); };
  }, [open, q, tries]);

  return (
    <Modal open={open} onClose={onClose} title="Dictate into a chat">
      <Stack gap="sm">
        <Input
          autoFocus
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search chats"
          aria-label="Search chats"
          data-dictation="off"
        />
        {failed ? (
          <StateBlock
            variant="error"
            title="Couldn't load chats"
            hint="GET /api/chat/threads"
            action={<Button size="sm" onClick={() => setTries((n) => n + 1)}>Retry</Button>}
          />
        ) : rows === null ? (
          <Stack gap="sm"><Skeleton /><Skeleton /><Skeleton /></Stack>
        ) : rows.length === 0 ? (
          <StateBlock title="No chats found" hint="Try other words from its title or its messages." />
        ) : (
          <ul className="ws-list">
            {rows.map((t) => (
              <li key={t.draftKey}>
                <button type="button" className="ws-listrow" onClick={() => onPick(t)}>
                  <span className="ws-listrow__mark"><MessageSquare size={14} aria-hidden /></span>
                  <span className="ws-listrow__title">{t.title}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Stack>
    </Modal>
  );
}
