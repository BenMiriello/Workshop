// Model identity, ordering, context windows, and per-model display tuning.
//
// Extracted from Chat.tsx, where four unrelated model concerns sat interleaved
// with 41 pieces of component state. None of this is stateful or React-aware:
// it is a lookup table with opinions, and it belongs where it can be read in one
// screen and changed without touching the chat.

// Display order for the model picker: most-reached-for first. The backend's list
// order drives default-model resolution, so this reorders the MENU only —
// anything the backend serves that isn't named here keeps its original position
// at the end, so a newly added model still shows up.
const MODEL_ORDER = [
  "claude-fable-5",
  "claude-opus-5",
  "claude-opus-4-8",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5-20251001",
];

export function orderModels(ids: string[]): string[] {
  const rank = (id: string) => {
    const i = MODEL_ORDER.indexOf(id);
    return i === -1 ? MODEL_ORDER.length : i;
  };
  return [...ids].sort((a, b) => rank(a) - rank(b));
}

export const MODEL_LABELS: Record<string, string> = {
  "claude-opus-5": "Opus 5",
  "claude-opus-4-8": "Opus 4.8",
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
  // TOTAL lines kept when a block is collapsed, split evenly above and below the
  // cut. Deliberately one number rather than separate head/tail counts: those
  // were 6 and 3, so a collapsed block showed twice as much before the break as
  // after it and the preview read as top-heavy. An even split can't drift.
  previewLines: number;
  elideOverLines: number;  // elide only when a block exceeds this many lines
  recentSteps: number;     // work steps shown inline on the most recent turn
};

const MODEL_DISPLAY: Record<string, ModelDisplay> = {
  default: { previewLines: 10, elideOverLines: 12, recentSteps: 2 },
};

export function modelDisplay(model?: string | null): ModelDisplay {
  return (model && MODEL_DISPLAY[model]) || MODEL_DISPLAY.default;
}

// The even split. Odd totals give the extra line to the head, because the first
// line of a block identifies it and the last is usually a fragment.
export function previewSplit(previewLines: number): { head: number; tail: number } {
  const head = Math.ceil(previewLines / 2);
  return { head, tail: previewLines - head };
}

// ---- Context window --------------------------------------------------------
// Kept in sync with backend/routes/chat.py `_CONTEXT_WINDOW` — every current
// Claude family is 1M, and Haiku 4.5 is the genuine 200k exception. These were
// all 200k, which made the meter read 155% (clamped to a confident 100%) on a
// chat using under a third of its window.
const CONTEXT_WINDOW: Record<string, number> = {
  "claude-opus-5": 1_000_000,
  "claude-opus-4-8": 1_000_000,
  "claude-opus-4-7": 1_000_000,
  "claude-opus-4-6": 1_000_000,
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
