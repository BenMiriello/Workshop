// The rules behind the model-default controls in Settings > Chat, kept out of
// the component so they can be run (modelDefaults.test.ts).
import type { components } from "../../api/schema.gen";

export type ModelOption = components["schemas"]["ModelOption"];
export type ScopeModelDefault = components["schemas"]["ScopeModelDefault"];

/** How many scopes show before the list is folded behind "Show all". */
export const SCOPES_SHOWN = 12;

/** A window in thousands of tokens, "" when nothing reported one. */
export function windowLabel(tokens: number): string {
  return tokens > 0 ? `${Math.round(tokens / 1000)}k` : "";
}

/** A model's name in a select, with its window when it is known. */
export function optionLabel(model: ModelOption): string {
  const w = windowLabel(model.window);
  return w ? `${model.label} (${w} window)` : model.label;
}

/** The name of a model id, from the served list, else the id itself. */
export function nameOf(id: string, models: readonly ModelOption[]): string {
  return models.find((m) => m.id === id)?.label ?? id;
}

/**
 * The empty choice of a scope's select: what the scope runs on without a
 * choice of its own. Named for where that answer comes from, since a scope
 * that declares a model falls back to it rather than to the global default.
 */
export function fallbackLabel(scope: ScopeModelDefault, models: readonly ModelOption[]): string {
  const name = nameOf(scope.fallback_model, models);
  if (scope.fallback_source === "scope") return `App default (${name})`;
  if (scope.fallback_source === "principal") return `Global default (${name})`;
  return `System default (${name})`;
}

/**
 * The empty choice of a scope's compaction select: the global compaction model
 * when one is set, else the chat's own model, which is what runs without one.
 */
export function compactionFallbackLabel(
  scope: ScopeModelDefault,
  candidates: readonly ModelOption[],
): string {
  const fallback = scope.compaction_fallback;
  return fallback ? `Global (${nameOf(fallback, candidates)})` : "Same as chat model";
}

/**
 * The options of a select, with a stored id the list no longer holds carried
 * at the top: without it the control would render blank, or show the first
 * option as though it were the setting.
 */
export function withStored(models: readonly ModelOption[], stored: string | null | undefined): ModelOption[] {
  if (!stored || models.some((m) => m.id === stored)) return [...models];
  return [{ id: stored, label: `${stored} (not available now)`, provider_id: "", window: 0 }, ...models];
}

/** The scopes to render: all of them once unfolded, else the first few. */
export function visibleScopes<T>(scopes: readonly T[], showAll: boolean): T[] {
  return showAll ? [...scopes] : scopes.slice(0, SCOPES_SHOWN);
}
