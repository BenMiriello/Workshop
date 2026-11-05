import { Fragment, type ReactNode } from "react";
import {
  CircleStop, Copy, MoreHorizontal, Pause, Pencil, Play, RotateCcw, Send, SendHorizontal, Trash2,
} from "lucide-react";
import { Button, Menu, MenuItem, MenuSeparator } from "@workshop/ui";
import type { Schedule } from "../../lib/chatSchedules";
import { actLabel, actsOf, groupOf, nameOf, type Act } from "./schedules";

// What can be done to a schedule: the menu rows its row in the list shows, and
// the buttons of its own view. Delete always comes last, below a separator.

function iconOf(a: Act, s: Schedule): ReactNode {
  const icons: Record<Exclude<Act, "toggle">, ReactNode> = {
    send: <Send size={14} aria-hidden />,
    edit: <Pencil size={14} aria-hidden />,
    duplicate: <Copy size={14} aria-hidden />,
    end: <CircleStop size={14} aria-hidden />,
    rearm: <RotateCcw size={14} aria-hidden />,
    again: <SendHorizontal size={14} aria-hidden />,
    delete: <Trash2 size={14} aria-hidden />,
  };
  if (a === "toggle") return s.status === "active" ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />;
  return icons[a];
}

export function ActItems({ s, acts, busy, onAct }: {
  s: Schedule;
  acts: readonly Act[];
  busy: boolean;
  onAct: (s: Schedule, act: Act) => void;
}) {
  const writes: readonly Act[] = ["send", "toggle", "end", "again"];
  return (
    <>
      {acts.map((a) => (
        <Fragment key={a}>
          {a === "delete" && acts.length > 1 && <MenuSeparator />}
          <MenuItem icon={iconOf(a, s)} danger={a === "delete"} disabled={busy && writes.includes(a)}
            onSelect={() => onAct(s, a)}>
            {actLabel(a, s)}
          </MenuItem>
        </Fragment>
      ))}
    </>
  );
}

/** A schedule's view: Duplicate and Delete behind the lead menu, then what
 *  its state offers, the usual next step as the primary button. */
export function ViewActions({ s, busy, onAct }: {
  s: Schedule;
  busy: boolean;
  onAct: (s: Schedule, act: Act) => void;
}) {
  const acts = actsOf(s);
  const has = (a: Act) => acts.includes(a);
  const button = (a: Act, primary = false) => has(a) && (
    <Button variant={primary ? "primary" : undefined} disabled={busy && a !== "edit" && a !== "rearm"}
      onClick={() => onAct(s, a)}>
      {actLabel(a, s)}
    </Button>
  );
  return (
    <>
      {has("duplicate") ? (
        <span className="ws-modal__actions-lead">
          <Menu label={`More for ${nameOf(s)}`} side="top" trigger={
            <Button variant="ghost" aria-label="More actions"><MoreHorizontal size={16} aria-hidden /></Button>
          }>
            <ActItems s={s} acts={["duplicate", "delete"]} busy={busy} onAct={onAct} />
          </Menu>
        </span>
      ) : (
        <Button className="ws-modal__actions-lead" variant="danger-ghost" onClick={() => onAct(s, "delete")}>
          Delete
        </Button>
      )}
      {groupOf(s) === "ended" ? (
        <>{button("again")}{button("rearm", true)}</>
      ) : (
        <>{button("end")}{button("toggle")}{button("edit", true)}</>
      )}
    </>
  );
}
