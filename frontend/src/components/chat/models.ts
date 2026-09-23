// Model identity, ordering, context windows, and per-model display tuning.
//
// Extracted from Chat.tsx, where four unrelated model concerns sat interleaved
// with 41 pieces of component state. None of this is stateful or React-aware:
// it is a lookup table with opinions, and it belongs where it can be read in one
// screen and changed without touching the chat.

// Display order for the model picker: grouped by family, Haiku block first,
// then Sonnet, then Opus, then Fable — operator's explicit call, 2026-09-22.
// Matched by PREFIX rather than a flat id list, so a family's next release
// (a new Haiku, a new Sonnet) sorts into its block on the day it ships, with
// no second edit here to remember.
const FAMILY_ORDER = ["claude-haiku", "claude-sonnet", "claude-opus", "claude-fable"];

// Within-family order, newest first. An id whose family isn't listed here
// falls in after its family's known ids but still inside that family's block;
// a family not in FAMILY_ORDER at all sorts after every named family.
const MODEL_ORDER = [
  "claude-haiku-4-5-20251001",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-opus-5-5",
  "claude-opus-5",
  "claude-fable-5-1",
  "claude-fable-5",
];

function familyRank(id: string): number {
  const i = FAMILY_ORDER.findIndex((family) => id.startsWith(family));
  return i === -1 ? FAMILY_ORDER.length : i;
}

export function orderModels(ids: string[]): string[] {
  const rank = (id: string) => {
    const i = MODEL_ORDER.indexOf(id);
    return i === -1 ? MODEL_ORDER.length : i;
  };
  return [...ids].sort((a, b) => {
    const fa = familyRank(a);
    const fb = familyRank(b);
    return fa !== fb ? fa - fb : rank(a) - rank(b);
  });
}

export const MODEL_LABELS: Record<string, string> = {
  "claude-opus-5-5": "Opus 5.5",
  "claude-opus-5": "Opus 5",
  "claude-opus-4-8": "Opus 4.8",
  "claude-fable-5-1": "Fable 5.1",
  "claude-fable-5": "Fable 5",
  "claude-sonnet-5": "Sonnet 5",
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};

export function modelLabel(id: string): string {
  return MODEL_LABELS[id] ?? id;
}

// ---- Per-model display handling -------------------------------------------
// How aggressively a model's turns collapse in the transcript. Keyed by model id
// with a `default` fallback, so a newly-added model can be tuned (or render
// differently) without touching the components.
export type ModelDisplay = {
  // How many AS-RENDERED lines a long block shows before it is clamped and
  // ended with a Show more (see ElidedContent). A block at or under this is
  // never clamped, so it doubles as the "long enough to elide" threshold.
  previewLines: number;
};

const MODEL_DISPLAY: Record<string, ModelDisplay> = {
  default: { previewLines: 10 },
};

export function modelDisplay(model?: string | null): ModelDisplay {
  return (model && MODEL_DISPLAY[model]) || MODEL_DISPLAY.default;
}

// ---- Context window --------------------------------------------------------
// Kept in sync with backend/routes/chat.py `_CONTEXT_WINDOW` — every current
// Claude family is 1M, and Haiku 4.5 is the genuine 200k exception. These were
// all 200k, which made the meter read 155% (clamped to a confident 100%) on a
// chat using under a third of its window.
const CONTEXT_WINDOW: Record<string, number> = {
  "claude-opus-5-5": 1_000_000,
  "claude-opus-5": 1_000_000,
  "claude-opus-4-8": 1_000_000,
  "claude-opus-4-7": 1_000_000,
  "claude-opus-4-6": 1_000_000,
  "claude-fable-5-1": 1_000_000,
  "claude-fable-5": 1_000_000,
  "claude-sonnet-5": 1_000_000,
  "claude-sonnet-4-6": 1_000_000,
  "claude-haiku-4-5": 200_000,
  "claude-haiku-4-5-20251001": 200_000,
};

export function contextWindowFor(model: string): number {
  if (CONTEXT_WINDOW[model]) return CONTEXT_WINDOW[model];
  // An unrecognized Claude model is likelier to be newer than older, and every
  // current family except Haiku is 1M. Guessing low is the failure that pins the
  // meter at 100%; guessing high just under-reports until a turn measures.
  if (model.startsWith("claude")) return 1_000_000;
  return model ? 32_768 : 1_000_000; // local/Ollama default; Claude if unset
}
