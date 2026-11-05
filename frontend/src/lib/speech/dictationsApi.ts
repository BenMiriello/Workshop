// Dictations on the server.
//
// While a dictation runs its pieces are kept there as they are said; the last
// day of them is what Settings > Speech lists. When it ends the client sends
// what was said and where it went; the server attaches the recording and tells
// every app that subscribes to `dictated` (a clipboard app keeps it). The
// answer says how many received it, which is what lets a device's old history
// be cleared once it is safe.

import type { components } from "../../api/schema.gen";

export type PlaceRef = { kind: "field" | "chat" | "clipboard"; name: string; path: string };

/** A piece as the server keeps it (lib/speech/pieces.ts). */
export type PieceBody = { place: PlaceRef; text: string; state: string; at: string };

/** A dictation still going, kept on the server as it is said, so a closed
 *  tab or a crash loses none of it. */
export type LiveDictationBody = { started_at: string; pieces: PieceBody[] };

/** A dictation the server kept, newest first (`GET /speech/dictations`). */
export type KeptDictation = components["schemas"]["KeptDictation"];

export type DictationBody = {
  id: string;
  text: string;
  places: PlaceRef[];
  started_at: string;
  ended_at: string;
  status: "done" | "cancelled";
  pieces?: PieceBody[];
  imported?: boolean;
};

export async function postDictation(body: DictationBody): Promise<number> {
  const r = await fetch("/api/speech/dictations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    keepalive: !body.imported,
  });
  if (!r.ok) throw new Error(`POST /api/speech/dictations ${r.status}`);
  const out = (await r.json()) as components["schemas"]["DictationDelivered"];
  return out.delivered;
}

export async function putDictation(id: string, body: LiveDictationBody): Promise<void> {
  const r = await fetch(`/api/speech/dictations/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PUT /api/speech/dictations ${r.status}`);
}

export async function keptDictations(): Promise<KeptDictation[]> {
  const r = await fetch("/api/speech/dictations");
  if (!r.ok) throw new Error(`GET /api/speech/dictations ${r.status}`);
  return ((await r.json()) as components["schemas"]["KeptDictations"]).dictations;
}

/** A dictation id: time-ordered, unguessable enough to name its recording. */
export function newDictationId(now = Date.now()): string {
  const rand = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
    : Math.random().toString(36).slice(2, 18);
  return `${now.toString(36)}-${rand}`;
}
