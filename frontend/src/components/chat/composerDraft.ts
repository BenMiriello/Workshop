import type { DraftSettings } from "../../lib/draftSync";

// WHICH DRAFT A COMPOSER IS TYPING INTO, and which of its saved picks to put
// back. Pure, so the rule can be run by a test instead of inferred from a
// component.
//
// The key is built from the SCOPE the chat resolved to, never from the route it
// was opened at. A blank chat at /chat/general resolves to `development` in a
// developer build, and keying by the route gave the same visible "new chat in
// development" two different drafts depending on how it was opened.

export function draftKeyFor(scope: string, activeId: string | null): string {
  return `${scope}::${activeId ?? "new"}`;
}

export type ComposerSettings = {
  model: string; effort: string; permission_mode: string; mode: string;
};

export type ValidChoices = {
  models: string[]; efforts: string[]; permissionModes: string[]; modes: string[];
};

/** The saved picks that differ from what the composer is showing and that this
 *  scope can honour. A field that was never chosen is absent from `saved` and is
 *  left on whatever default the composer already resolved. */
export function settingsToApply(
  saved: DraftSettings, current: ComposerSettings, valid: ValidChoices,
): Partial<ComposerSettings> {
  const out: Partial<ComposerSettings> = {};
  if (saved.model && saved.model !== current.model && valid.models.includes(saved.model)) {
    out.model = saved.model;
  }
  // Only a level the current model offers. The picker strips a level the model
  // lacks; restoring one it just stripped would have the two fight forever.
  if (saved.effort && saved.effort !== current.effort && valid.efforts.includes(saved.effort)) {
    out.effort = saved.effort;
  }
  if (
    saved.permission_mode && saved.permission_mode !== current.permission_mode
    && valid.permissionModes.includes(saved.permission_mode)
  ) {
    out.permission_mode = saved.permission_mode;
  }
  if (saved.mode && saved.mode !== current.mode && valid.modes.includes(saved.mode)) {
    out.mode = saved.mode;
  }
  return out;
}
