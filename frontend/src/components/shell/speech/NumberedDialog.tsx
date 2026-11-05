// The numbered list showing (numbered.ts): Commands, or a list of choices
// ("Did you mean", the places to open, the models). Each row is its number,
// what it does and how it is said; saying the number runs it.
//
// In Commands a row is read, not pressed: saying its number runs it, and a
// row said more than one way has a chevron that shows the other ways. A list
// of choices is a picker: a click on a row picks it.

import { useState, useSyncExternalStore } from "react";
import { ChevronRight, Search, Settings2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button, Input, Modal, SectionLabel } from "@workshop/ui";
import { filterGroups, numbered, type NumberedItem } from "./numbered";
import "./numbered.css";

function CommandRow({ item, open, onToggle }: { item: NumberedItem; open: boolean; onToggle: () => void }) {
  const more = (item.phrases?.length ?? 0) > 1;
  return (
    <div className="ws-numbered__row" data-open={open || undefined}>
      <span className="ws-numbered__n">{item.n}</span>
      <div className="ws-numbered__main">
        <span className="ws-numbered__text">{item.label}</span>
        {item.hint && <span className="ws-numbered__say">{item.hint}</span>}
        {more ? (
          <button type="button" className="ws-numbered__more" aria-expanded={open} onClick={onToggle}
            aria-label={open ? "Hide other ways to say it" : "Show other ways to say it"}>
            <ChevronRight className="ws-numbered__more-icon" aria-hidden />
          </button>
        ) : <span className="ws-numbered__more" aria-hidden />}
      </div>
      {open && more && (
        <div className="ws-numbered__phrases">
          {item.phrases!.slice(1).map((p) => <span key={p} className="ws-numbered__say">{p}</span>)}
        </div>
      )}
    </div>
  );
}

function ChoiceRow({ item }: { item: NumberedItem }) {
  return (
    <button type="button" className="ws-numbered__choice" onClick={() => numbered.pick(item.n!)} disabled={item.n === null}>
      <span className="ws-numbered__n">{item.n}</span>
      <span className="ws-numbered__text">{item.label}</span>
      {item.hint && <span className="ws-numbered__say">{item.hint}</span>}
    </button>
  );
}

export function NumberedDialog() {
  const list = useSyncExternalStore(numbered.subscribe, numbered.get);
  const navigate = useNavigate();
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const commands = list?.kind === "commands";
  const close = () => { numbered.close(); setOpen(null); setQ(""); };
  const manage = () => { close(); navigate("/settings/speech#commands"); };
  const groups = list ? filterGroups(list.groups, commands ? q : "") : [];
  return (
    <Modal
      open={!!list}
      onClose={close}
      title={list?.title ?? ""}
      size={commands ? "lg" : "default"}
      actions={commands ? (
        <>
          <Button className="ws-modal__actions-lead" variant="ghost" onClick={manage}>
            <Settings2 aria-hidden /> Manage commands
          </Button>
          <Button variant="ghost" onClick={close}>Close</Button>
        </>
      ) : undefined}
    >
      {/* Focus starts on the list, not the filter, so a phone does not open
          its keyboard over the list. */}
      <div className={"ws-numbered" + (commands ? " ws-numbered--commands" : "")} data-dictation="off"
        tabIndex={-1} data-autofocus>
        {commands && (
          <div className="ws-numbered__filter">
            <Search aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" aria-label="Filter commands"
              data-dictation="off" />
          </div>
        )}
        {commands && !groups.length && <p className="ws-numbered__none">No command matches "{q.trim()}"</p>}
        {groups.map((g) => (
          <section key={g.label} aria-label={g.label}>
            <SectionLabel>{g.label}</SectionLabel>
            {g.items.map((i) => {
              const key = `${g.label}:${i.label}`;
              return commands
                ? <CommandRow key={key} item={i} open={open === key} onToggle={() => setOpen(open === key ? null : key)} />
                : <ChoiceRow key={key} item={i} />;
            })}
          </section>
        ))}
      </div>
    </Modal>
  );
}
