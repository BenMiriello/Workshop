import { userStorage } from "../../../lib/storage";

// THE CONVERSATION YOU LAST HAD BESIDE EACH PLACE, on this device, so opening
// the drawer there again, in a later session too, brings it back whatever scope
// it is filed under. Keyed by `ShellPlace.id`.
const LAST_KEY = "ws-beside-last";
const LAST_CAP = 40;

type Last = { threadId: string; scope: string; at: number };

function readLast(): Record<string, Last> {
  try {
    const raw = JSON.parse(userStorage.local.getItem(LAST_KEY) || "{}");
    return raw && typeof raw === "object" ? raw as Record<string, Last> : {};
  } catch {
    return {};
  }
}

// Keys written before the drawer was per place: an app's slug, "workshop".
const legacyKey = (place: string) => (place === "home" ? "workshop" : place.replace(/^app:/, ""));

export function lastBeside(place: string): Last | null {
  const all = readLast();
  const hit = all[place] ?? all[legacyKey(place)];
  return hit && typeof hit.threadId === "string" && typeof hit.scope === "string" ? hit : null;
}

export function rememberBeside(page: string, threadId: string, scope: string): void {
  const all = readLast();
  if (all[page]?.threadId === threadId && all[page]?.scope === scope) return;
  all[page] = { threadId, scope, at: Date.now() };
  const kept = Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, LAST_CAP);
  userStorage.local.setItem(LAST_KEY, JSON.stringify(Object.fromEntries(kept)));
}
