// The action history as the operator sees it: a History section at the foot of
// the Actions menu (the newest five, Undo and Redo on the heading and on each
// row, an undone row greyed), and every kept action in a dialog. Opening
// either asks each action whether it can still be undone or redone.

import { useEffect, useSyncExternalStore } from "react";
import { History, Loader2, Redo2, Undo2 } from "lucide-react";
import { Button, MenuAction, MenuItem, MenuLabel, MenuRow, MenuSeparator, Modal, Stack } from "@workshop/ui";
import { actionHistory, type ActionEntry } from "./actionHistory";
import { redoAction, undoAction } from "./actionRun";
import { dictation } from "./dictation";

const SHOWN = 5;

function useHistory(open = true): readonly ActionEntry[] {
  const list = useSyncExternalStore(actionHistory.subscribe, () => actionHistory.entries);
  useEffect(() => { if (open) void actionHistory.refresh(); }, [open]);
  return list;
}

const undo = (id: number) => void undoAction(dictation, id);
const redo = (id: number) => void redoAction(dictation, id);

function EntryAction({ e }: { e: ActionEntry }) {
  if (e.busy) return <MenuAction icon={<Loader2 className="ws-spin" aria-hidden />} label="Working" disabled onPress={() => {}} />;
  return e.done
    ? <MenuAction icon={<Undo2 aria-hidden />} label="Undo" title={e.blocked.undo ?? "Undo"} disabled={!!e.blocked.undo} onPress={() => undo(e.id)} />
    : <MenuAction icon={<Redo2 aria-hidden />} label="Redo" title={e.blocked.redo ?? "Redo"} disabled={!!e.blocked.redo} onPress={() => redo(e.id)} />;
}

function Heading() {
  const u = actionHistory.newestUndo();
  const r = actionHistory.newestRedo();
  return (
    <MenuLabel actions={(
      <>
        <MenuAction icon={<Undo2 aria-hidden />} label={u ? `Undo ${u.label}` : "Nothing to undo"} disabled={!u} onPress={() => u && undo(u.id)} />
        <MenuAction icon={<Redo2 aria-hidden />} label={r ? `Redo ${r.label}` : "Nothing to redo"} disabled={!r} onPress={() => r && redo(r.id)} />
      </>
    )}
    >
      History
    </MenuLabel>
  );
}

/** The menu's History section; nothing until an action has been taken. */
export function HistorySection({ onAll }: { onAll: () => void }) {
  const list = useHistory();
  if (!list.length) return null;
  const shown = list.slice(-SHOWN).reverse();
  return (
    <>
      <MenuSeparator />
      <Heading />
      {shown.map((e) => (
        <MenuRow key={e.id} muted={!e.done} actions={<EntryAction e={e} />}>{e.label}</MenuRow>
      ))}
      {list.length > SHOWN && (
        <MenuItem icon={<History aria-hidden />} onSelect={onAll}>All actions…</MenuItem>
      )}
    </>
  );
}

/** Every kept action, newest first. */
export function AllActions({ open, onClose }: { open: boolean; onClose: () => void }) {
  const list = useHistory(open);
  return (
    <Modal open={open} onClose={onClose} title="Actions" actions={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      <Stack className="ws-actlog">
        {[...list].reverse().map((e) => (
          <div key={e.id} className={"ws-actlog__row" + (e.done ? "" : " is-undone")}>
            <span className="ws-actlog__label">{e.label}</span>
            <EntryAction e={e} />
          </div>
        ))}
        {!list.length && <span className="ws-actlog__none">No actions yet.</span>}
      </Stack>
    </Modal>
  );
}
