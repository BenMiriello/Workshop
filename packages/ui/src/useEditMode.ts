import { useCallback, useState } from "react";

// The editability model: a group has two modes, View (read-only) and Edit
// (controls unlocked + sticky Save/Cancel). Nothing commits by accident — Save
// writes a draft and flashes "Saved ✓"; Cancel discards. Use for anything that
// writes config or money (budget, coords, prompts, cron, secrets).
//
//   const edit = useEditMode(model, async (draft) => { await save(draft); });
//   edit.editing ? <input value={edit.draft.x} onChange=...> : <ReadOnly value={model.x}/>
//
// CONTRACT: save() never rejects. If onSave throws, the error is captured in
// saveError and the hook stays in edit mode so the operator can fix and retry.
// Callers render <Notice> when edit.saveError is set; they do not .catch() the
// returned promise. If the onSave callback sets its own error state (for a more
// specific message), it should rethrow so the hook also stays in edit mode.
export interface EditMode<T> {
  editing: boolean;
  draft: T;
  dirty: boolean;
  saving: boolean;
  flash: boolean;
  /** The error from the last save attempt, or null. Cleared by begin(), cancel(),
   *  and a successful save. Render a <Notice> when this is set. */
  saveError: string | null;
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

/** The save step, isolated for testing. Returns null on success, or the error
 *  message string when onSave throws. Never rejects itself. */
export async function runSave<T>(
  draft: T,
  onSave: (d: T) => Promise<void> | void,
): Promise<string | null> {
  try {
    await onSave(draft);
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

export function useEditMode<T extends object>(current: T, onSave: (draft: T) => Promise<void> | void): EditMode<T> {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<T>(current);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const begin = useCallback(() => {
    setDraft({ ...current });
    setEditing(true);
    setFlash(false);
    setSaveError(null);
  }, [current]);

  const cancel = useCallback(() => {
    setEditing(false);
    setDraft(current);
    setSaveError(null);
  }, [current]);

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  }, []);

  const patch = useCallback((partial: Partial<T>) => {
    setDraft((d) => ({ ...d, ...partial }));
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    const err = await runSave(draft, onSave);
    setSaving(false);
    if (err !== null) {
      setSaveError(err);
    } else {
      setSaveError(null);
      setEditing(false);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
    }
  }, [draft, onSave]);

  return {
    editing,
    draft,
    dirty: editing && !shallowEqual(draft, current),
    saving,
    flash,
    saveError,
    begin,
    cancel,
    set,
    patch,
    save,
  };
}
