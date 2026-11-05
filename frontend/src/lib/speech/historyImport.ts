// Sending this device's old dictation history on to the clipboard app, once.
//
// Each past dictation goes out as a `dictated` event marked imported, keeping
// its own times. The device's copy is cleared only when every one of them
// reached at least one subscriber, so a Workshop with no clipboard app running
// keeps the history until there is somewhere for it to go.

import { userStorage } from "../storage";
import { postDictation, type DictationBody } from "./dictationsApi";
import { HISTORY_KEEP_KEY, HISTORY_KEY, parseHistory, type DictationRecord } from "./history";

let tried = false;

export function importBody(r: DictationRecord): DictationBody {
  return {
    id: r.id,
    text: r.text,
    places: r.places.map((name) => ({ kind: "field", name, path: "" })),
    started_at: new Date(r.startedAt).toISOString(),
    ended_at: new Date(r.endedAt ?? r.startedAt).toISOString(),
    status: r.status === "cancelled" ? "cancelled" : "done",
    imported: true,
  };
}

export async function importHistory(): Promise<void> {
  if (tried) return;
  tried = true;
  const list = parseHistory(userStorage.local.getItem(HISTORY_KEY)).filter((r) => r.text.trim());
  if (!list.length) {
    userStorage.local.removeItem(HISTORY_KEY);
    userStorage.local.removeItem(HISTORY_KEEP_KEY);
    return;
  }
  let all = true;
  for (const r of [...list].sort((a, b) => a.startedAt - b.startedAt)) {
    const delivered = await postDictation(importBody(r)).catch(() => 0);
    if (delivered < 1) { all = false; break; }
  }
  if (!all) return;
  userStorage.local.removeItem(HISTORY_KEY);
  userStorage.local.removeItem(HISTORY_KEEP_KEY);
}
