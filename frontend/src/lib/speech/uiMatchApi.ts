// The server's ranking of the controls on screen by what a spoken click
// means (backend/services/speech/ui_match.py), with its thresholds.

import type { components } from "../../api/schema.gen";
import { jsonOrFail } from "../http";

export type SpeechMatch = components["schemas"]["SpeechMatch"];

const MAX_ITEMS = 200;

/** Null when the server cannot be asked; `scores` is null when it has no model. */
export async function rankControls(query: string, items: string[]): Promise<SpeechMatch | null> {
  try {
    const r = await fetch("/api/speech/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, items: items.slice(0, MAX_ITEMS) }),
    });
    return await jsonOrFail<SpeechMatch>(r);
  } catch {
    return null;
  }
}
