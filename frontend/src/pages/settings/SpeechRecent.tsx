// The last day of dictations the platform keeps (GET /api/speech/dictations),
// each as its pieces: where every run of words went, with Copy. This is how
// words are found again after a tab closed or a page crashed mid-dictation,
// whether or not an app keeps dictations.

import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import { Button, Modal, Skeleton, Stack, StateBlock, copyText } from "@workshop/ui";
import { keptDictations, type KeptDictation, type PieceBody } from "../../lib/speech/dictationsApi";
import { joinSaid } from "../../lib/speech/insertText";

const STATUS: Record<KeptDictation["status"], string> = {
  live: "Going now", done: "", cancelled: "Cancelled", interrupted: "Ended without Stop",
};

function where(p: PieceBody): string {
  const name = p.place.name;
  switch (p.state) {
    case "sent": return `Sent to ${name}`;
    case "waiting": case "unplaced": return "Not placed";
    case "clipboard": return "Clipboard";
    default: return name;
  }
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function CopyIcon({ text }: { text: string }) {
  return (
    <Button size="sm" variant="ghost" icon aria-label="Copy" title="Copy" onClick={() => void copyText(text)}>
      <Copy aria-hidden />
    </Button>
  );
}

function Dictation({ d }: { d: KeptDictation }) {
  const all = d.pieces.reduce((t, p) => joinSaid(t, p.text), "");
  const status = STATUS[d.status];
  return (
    <div className="ws-recent">
      <div className="ws-recent__head">
        <span>{time(d.started_at)}{status ? ` · ${status}` : ""}</span>
        {d.pieces.length > 1 && <CopyIcon text={all} />}
      </div>
      {d.pieces.map((p, i) => (
        <div key={i} className="ws-pieces__row">
          <div className="ws-pieces__body">
            <span className="ws-pieces__where">{where(p)}</span>
            <span className="ws-pieces__text">{p.text}</span>
          </div>
          <CopyIcon text={p.text} />
        </div>
      ))}
    </div>
  );
}

export function SpeechRecent({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [list, setList] = useState<KeptDictation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    setError(null);
    keptDictations().then(setList, (e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(() => { if (open) load(); }, [open]);
  return (
    <Modal open={open} onClose={onClose} title="Dictations, last day"
      actions={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      {error ? <StateBlock variant="error" title="Could not load dictations" hint={error}
        action={<Button size="sm" onClick={load}>Retry</Button>} />
        : !list ? <Stack gap="sm"><Skeleton /><Skeleton width="70%" /></Stack>
        : !list.length ? <StateBlock title="No dictations in the last day" hint="Dictate with the mic or the shortcut." />
        : <Stack className="ws-pieces">{list.map((d) => <Dictation key={d.id} d={d} />)}</Stack>}
    </Modal>
  );
}
