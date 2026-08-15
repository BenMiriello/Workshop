// The editable pieces of the ticket page.
//
// NO PAGE-WIDE EDIT MODE. That is the redesign's one structural change to this
// page and it is worth stating why, because the mode it replaces was itself a
// deliberate choice: config commits on an explicit Save (frontend/CLAUDE.md).
//
// What the mode actually cost: changing a priority was Edit, type, Save, three
// interactions and a whole-page state change to write one number, and while it
// was open every OTHER field on the page was a form control the operator was
// not using. It also produced a real bug class, which is why Status was already
// carved out of it: two writers with different commit rules means a Save can
// silently revert a field that was committed under the other rule.
//
// Per field, the rule holds in a smaller box, and each field says which it is:
//   • a PICK (status, effort, execution, latitude) commits on change. Choosing
//     the value IS the explicit act, it is one deliberate interaction, and the
//     new value is on screen immediately and reversible in one more.
//   • TYPED text (title, description, priority, dependencies) commits on Save,
//     with Cancel beside it, because a half-typed sentence must never be a
//     write and there is no "the value is right there" until you stop typing.
// Every save says what it saved and shows its own failure at the field, not as
// a page banner.
import { useEffect, useState, type ReactNode } from "react";
import { Check, Pencil, X } from "lucide-react";
import { Button, Input, Notice, Select, Textarea } from "@workshop/ui";

/** A sidebar row: label, value, and an editor that replaces the value in
 *  place. Read state is a button so it is reachable by keyboard and announces
 *  itself as editable; nothing about it looks like a form until it is one. */
export function PropRow({
  label,
  value,
  editor,
  editable = true,
}: {
  label: ReactNode;
  value: ReactNode;
  /** Given a `done` callback to close the row. Omit for a read-only row. */
  editor?: (done: () => void) => ReactNode;
  editable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const canEdit = editable && !!editor;
  return (
    <div className={`ws-proprow${open ? " is-editing" : ""}`}>
      <span className="ws-proprow__label">{label}</span>
      {open && editor ? (
        <span className="ws-proprow__editor">{editor(() => setOpen(false))}</span>
      ) : canEdit ? (
        <button type="button" className="ws-proprow__value" onClick={() => setOpen(true)}>
          {value}
          <Pencil className="ws-proprow__pencil" aria-hidden />
        </button>
      ) : (
        <span className="ws-proprow__value ws-proprow__value--static">{value}</span>
      )}
    </div>
  );
}

/** A `<select>` that writes as soon as it is changed, then closes its row. */
export function PickEditor({
  value,
  options,
  onPick,
  done,
  label,
}: {
  value: string;
  options: { value: string; label: string }[];
  onPick: (next: string) => void;
  done: () => void;
  label: string;
}) {
  return (
    <Select
      aria-label={label}
      value={value}
      autoFocus
      onChange={(e) => { onPick(e.target.value); done(); }}
      onBlur={done}
    >
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </Select>
  );
}

/** A one-line text/number editor. Enter saves, Escape cancels. */
export function TextEditor({
  value,
  onSave,
  done,
  label,
  inputMode,
  placeholder,
}: {
  value: string;
  onSave: (next: string) => void;
  done: () => void;
  label: string;
  inputMode?: "numeric" | "text";
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  const save = () => { if (draft !== value) onSave(draft); done(); };
  return (
    <span className="ws-inlineedit">
      <Input
        aria-label={label}
        value={draft}
        autoFocus
        inputMode={inputMode}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); save(); }
          if (e.key === "Escape") { e.preventDefault(); done(); }
        }}
      />
      <Button size="sm" variant="ghost" aria-label="Save" onClick={save}>
        <Check aria-hidden />
      </Button>
      <Button size="sm" variant="ghost" aria-label="Cancel" onClick={done}>
        <X aria-hidden />
      </Button>
    </span>
  );
}

/** The title: reads as a heading until you click it. */
export function EditableTitle({
  value,
  onSave,
  busy,
  error,
}: {
  value: string;
  onSave: (next: string) => void;
  busy?: boolean;
  error?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => { if (!open) setDraft(value); }, [value, open]);

  if (!open) {
    return (
      <button type="button" className="ws-titleedit" onClick={() => setOpen(true)} title="Edit title">
        {value || "(untitled)"}
      </button>
    );
  }
  const clean = draft.replace(/\r?\n/g, " ").trim();
  return (
    <span className="ws-titleedit__form">
      <Input
        aria-label="Ticket title"
        value={draft}
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && clean) { e.preventDefault(); onSave(draft); setOpen(false); }
          if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
        }}
      />
      <Row2>
        <Button size="sm" variant="primary" loading={busy} disabled={!clean}
          onClick={() => { onSave(draft); setOpen(false); }}>Save</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        {!clean && <span className="ws-field__error">A ticket needs a title.</span>}
      </Row2>
      {error && <Notice tone="error">{error}</Notice>}
    </span>
  );
}

// A bare flex row. Not `Row`, which is a kit primitive with its own gap scale:
// this sits inside an editor and wants the tighter one.
function Row2({ children }: { children: ReactNode }) {
  return <span className="ws-inlineedit__actions">{children}</span>;
}

/** The description: rendered content until you click it, then a textarea with
 *  its own Save. */
export function EditableBody({
  value,
  render,
  onSave,
  busy,
  error,
}: {
  value: string;
  /** How to show the saved value (markdown, in practice). */
  render: ReactNode;
  onSave: (next: string) => void;
  busy?: boolean;
  error?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => { if (!open) setDraft(value); }, [value, open]);

  if (!open) {
    return (
      <div className="ws-bodyedit">
        <button
          type="button"
          className="ws-bodyedit__read"
          onClick={() => setOpen(true)}
          title="Edit description"
        >
          {value ? render : <span className="ws-muted">No description. Click to add one.</span>}
        </button>
      </div>
    );
  }
  return (
    <div className="ws-bodyedit">
      <Textarea
        aria-label="Description"
        autoFocus
        rows={12}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
      />
      <div className="ws-inlineedit__actions">
        <Button size="sm" variant="primary" loading={busy}
          onClick={() => { onSave(draft); setOpen(false); }}>Save</Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        <span className="ws-inlineedit__hint">Saves this field only.</span>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
