// "This dictation": every piece of the dictation running now, newest first,
// each saying where its words went, with Copy and a Move to menu. A piece
// that can no longer be taken out of its place (sent, edited, or in an editor
// that keeps its own text) offers Copy to instead, so nothing is claimed that
// did not happen.

import { useEffect, useState } from "react";
import { Copy, CornerDownRight } from "lucide-react";
import { Button, Menu, MenuItem, MenuLabel, Modal, Stack, copyText } from "@workshop/ui";
import { dictation, type DictationState } from "./dictation";
import { samePlace, type Destination } from "./destination";
import { takeOutBlocked } from "./pieceMoves";
import { KIND_ICON, places } from "./PillMenu";
import { sinkFor } from "./sinkFor";
import type { DictationPiece } from "./transcript";

export function whereText(p: DictationPiece): string {
  const name = p.place.name;
  switch (p.state) {
    case "sent": return `Sent to ${name}`;
    case "waiting": return `Waiting for ${name}`;
    case "unplaced": return "Not placed. Copied at Stop.";
    case "clipboard": return "Clipboard. Copied at Stop.";
    default: return `In ${name}`;
  }
}

// Places a piece can be put in now: on screen, a chat, or the clipboard.
const takes = (d: Destination) => sinkFor(d).sink.kind !== "wait";

function Row({ p, s }: { p: DictationPiece; s: DictationState }) {
  const [blocked, setBlocked] = useState<string | null>(null);
  useEffect(() => { void takeOutBlocked(p).then(setBlocked); }, [p]);
  const verb = blocked ? "Copy to" : "Move to";
  const go = (d: Destination) => void dictation.movePiece(p, d).then((r) => {
    if (r === "failed") dictation.notice({ text: `Could not put the words in ${d.name}` });
    else dictation.notice({ text: `${r === "moved" ? "Moved" : "Copied"} to ${d.name}` });
  });
  const Icon = KIND_ICON[p.place.kind];
  return (
    <div className="ws-pieces__row">
      <Icon className="ws-pieces__icon" aria-hidden />
      <div className="ws-pieces__body">
        <span className="ws-pieces__where">{whereText(p)}</span>
        <span className="ws-pieces__text">{p.text}</span>
      </div>
      <Button size="sm" variant="ghost" icon aria-label="Copy" title="Copy" onClick={() => void copyText(p.text)}>
        <Copy aria-hidden />
      </Button>
      <Menu
        label={verb}
        trigger={(
          <Button size="sm" variant="ghost" title={blocked ? `${verb} (${blocked})` : verb}>
            <CornerDownRight aria-hidden /> {verb}
          </Button>
        )}
      >
        <MenuLabel>{verb}</MenuLabel>
        {places(s).filter((d) => !samePlace(d, p.dest) && takes(d)).map((d) => {
          const DIcon = KIND_ICON[d.kind];
          return <MenuItem key={d.name + d.kind} icon={<DIcon aria-hidden />} onSelect={() => go(d)}>{d.name}</MenuItem>;
        })}
      </Menu>
    </div>
  );
}

export function PiecesDialog({ open, onClose, state }: { open: boolean; onClose: () => void; state: DictationState }) {
  const list = [...state.pieces].reverse();
  return (
    <Modal open={open} onClose={onClose} title="This dictation"
      actions={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      <Stack className="ws-pieces">
        {list.map((p) => <Row key={p.id} p={p} s={state} />)}
        {!list.length && <span className="ws-pieces__none">Nothing said yet.</span>}
      </Stack>
    </Modal>
  );
}
