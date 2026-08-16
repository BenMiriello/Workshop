// WHO CONSUMES A CARD'S VALUES DECIDES WHO OWNS ITS FIELDS.
//
// A ui-card is authored by the model as free-form JSON, and the renderer used to
// draw whatever field names arrived. Nothing validated a name against anything,
// so the only thing standing between the model and a bogus field was a sentence
// of prose in the prompt asking it not to emit one ("Do NOT emit priority,
// effort, execution, labels or any other field", chat_context.TASK_PROPOSAL_CARD).
// That prohibition was a substitute for a schema, and it failed the way prose
// always does: a chat holding a system prompt from before the contract changed
// kept emitting the old eight-field card for hours, and every field outside the
// filing whitelist rendered as a real editable input whose value was discarded.
//
// The rule this module encodes:
//
//   store        the values become a RECORD in a store (a ticket, a project).
//                The CLIENT owns the field list. The model supplies VALUES, not
//                a schema, and a name the client does not know cannot render at
//                all. This is where determinism is mandatory: a stored record
//                has a shape, and a model guessing at that shape is a wire
//                format defined by prose.
//
//   conversation the values come back to the MODEL as text (a clarifying
//                questionnaire, a confirmation, a "which of these did you
//                mean"). Free-form fields are legitimate here, because the model
//                is the only consumer and nothing downstream can be corrupted by
//                an odd key. The answer is just text in the next turn.
//
// NO KIND IS `conversation` TODAY, and none is invented here. The distinction is
// recorded because it is the line worth holding, not because there is a
// questionnaire waiting to ship: adding one later is an entry in this table, and
// it inherits the fixed widget vocabulary (UiCardFieldType) either way. The model
// may invent QUESTIONS; it never invents RENDERING, and it never invents the
// shape of a stored record.
//
// `project-proposal` is deliberately absent. It is described in no prompt, so
// nothing emits one; it survives only in old transcripts, and a kind with no
// schema keeps the permissive legacy rendering rather than being handed a
// contract invented after the fact.

import { SPEC_SECTIONS, type UiCardField, type UiCardSpec } from "./uiCardActions";

export type CardBinding = "store" | "conversation";

export type CardSchema = {
  binding: CardBinding;
  /** The fields this kind has. For a store-bound kind this list is exhaustive:
   *  the model cannot add to it and cannot rename out of it. */
  fields: UiCardField[];
};

// A ticket is a title and a description. Everything else about a ticket
// (priority, effort, execution, labels) is edited on the ticket itself, so it is
// not on the card: a second place to set a value is a second place for two
// values to disagree.
export const CARD_SCHEMAS: Record<string, CardSchema> = {
  "task-proposal": {
    binding: "store",
    fields: [
      { name: "scope", label: "Scope", type: "taskscope" },
      { name: "title", label: "Title", type: "text" },
      { name: "description", label: "Description", type: "textarea" },
    ],
  },
};

export function cardSchemaFor(kind: string | undefined): CardSchema | null {
  if (!kind) return null;
  return CARD_SCHEMAS[kind] ?? null;
}

/** The legacy one-field-per-section keys. Their content is folded into
 *  `description` on render, so they must NOT also survive as carried values:
 *  `composeTaskText` prefers sections over a description when both are present,
 *  which would silently discard an edit the operator made in the box. */
const LEGACY_SECTION_KEYS: ReadonlySet<string> = new Set(SPEC_SECTIONS.map((s) => s.key as string));

/** Fold a legacy card's section fields into one description body, using the same
 *  labels and the same order `composeTaskText` would have filed them under, so
 *  what the operator reads is what gets filed. */
function descriptionFromSections(byName: Map<string, string>): string {
  const lines: string[] = [];
  for (const s of SPEC_SECTIONS) {
    const v = (byName.get(s.key as string) || "").trim();
    if (v) lines.push(`${s.label}: ${v}`);
  }
  return lines.join("\n\n");
}

export type NormalizedCard = {
  /** The spec to render: canonical fields for a store-bound kind. */
  spec: UiCardSpec;
  /** Values the model sent that the schema has no field for, kept out of the UI
   *  but still handed to the action handler. This is what lets a card stored in
   *  an older transcript file exactly as it always did (its `priority` and
   *  `effort` still reach the request) without those fields reappearing as
   *  editable controls on a card written today. */
  carried: Record<string, string>;
};

/**
 * Put a model-authored card spec onto its kind's schema.
 *
 * For a store-bound kind the returned field list IS the schema's, in the
 * schema's order and with the schema's types. The model may still supply the
 * human copy (`label`, `help`) and the `options` of a select, because that is
 * content rather than shape; it may not add, remove, rename, or retype a field.
 *
 * A kind with no schema is returned untouched.
 */
export function normalizeCardSpec(spec: UiCardSpec | null): NormalizedCard | null {
  if (!spec) return null;
  const schema = cardSchemaFor(spec.kind);
  if (!schema || schema.binding !== "store") return { spec, carried: {} };

  const supplied = spec.fields ?? [];
  const byField = new Map<string, UiCardField>();
  const byName = new Map<string, string>();
  for (const f of supplied) {
    if (!f || typeof f.name !== "string") continue;
    byField.set(f.name, f);
    byName.set(f.name, f.value ?? "");
  }

  const known = new Set(schema.fields.map((f) => f.name));
  const fields: UiCardField[] = schema.fields.map((sf) => {
    const from = byField.get(sf.name);
    let value = byName.get(sf.name) ?? "";
    // A card written before a ticket was a title and a description carries its
    // spec as one field per section. Fold those into the description so the
    // whole spec is visible and editable in the box that files it.
    if (sf.name === "description" && !value.trim()) {
      value = descriptionFromSections(byName);
    }
    return {
      ...sf,
      label: from?.label ?? sf.label,
      help: from?.help ?? sf.help,
      options: sf.type === "select" ? (from?.options ?? sf.options) : sf.options,
      value,
    };
  });

  const carried: Record<string, string> = {};
  for (const [name, value] of byName) {
    if (known.has(name)) continue;
    if (LEGACY_SECTION_KEYS.has(name)) continue;
    carried[name] = value;
  }

  return { spec: { ...spec, fields }, carried };
}
