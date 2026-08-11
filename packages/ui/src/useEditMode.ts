import { useCallback, useState } from "react";

// The editability model: a group has two modes, View (read-only) and Edit
// (controls unlocked + sticky Save/Cancel). Nothing commits by accident — Save
// writes a draft and flashes "Saved ✓"; Cancel discards. Use for anything that
// writes config or money (budget, coords, prompts, cron, secrets).
//
//   const edit = useEditMode(model, async (draft) => { await save(draft); });
//   edit.editing ? <input value={edit.draft.x} onChange=...> : <ReadOnly value={model.x}/>
export interface EditMode<T> {
  editing: boolean;
  draft: T;
  dirty: boolean;
  saving: boolean;
  flash: boolean;
  begin: () => void;
  cancel: () => void;
  set: <K extends keyof T>(key: K, value: T[K]) => void;
  patch: (partial: Partial<T>) => void;
  save: () => Promise<void>;
}

function shallowEqual<T extends object>(a: T, b: T): boolean {
  const ka = Object.keys(a) as Array<keyof T>;
  const kb = Object.keys(b) as Array<keyof T>;
  if (ka.length !== kb.length) return false;
  return ka.every((k) => a[k] === b[k]);
}

export function useEditMode<T extends object>(current: T, onSave: (draft: T) => Promise<void> | void): EditMode<T> {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<T>(current);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);

  const begin = useCallback(() => {
    setDraft({ ...current });
    setEditing(true);
    setFlash(false);
  }, [current]);

  const cancel = useCallback(() => {
    setEditing(false);
    setDraft(current);
  }, [current]);

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  }, []);

  const patch = useCallback((partial: Partial<T>) => {
    setDraft((d) => ({ ...d, ...partial }));
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await onSave(draft);
      setEditing(false);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
    } finally {
      setSaving(false);
    }
  }, [draft, onSave]);

  return {
    editing,
    draft,
    dirty: editing && !shallowEqual(draft, current),
    saving,
    flash,
    begin,
    cancel,
    set,
    patch,
    save,
  };
}
