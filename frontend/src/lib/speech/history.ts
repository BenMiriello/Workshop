// The dictation history this device kept before dictations went to the
// clipboard app (Settings > Speech used to list it). Read only to send it on,
// once (historyImport.ts).

export type DictationStatus = "live" | "done" | "cancelled";

export type DictationRecord = {
  id: string;
  startedAt: number;
  endedAt: number | null;
  text: string;
  /** Where the words went, in order, each named once. */
  places: string[];
  status: DictationStatus;
};

export const HISTORY_KEY = "speech.history";
export const HISTORY_KEEP_KEY = "speech.history.keep";

function isRecord(r: unknown): r is DictationRecord {
  const x = r as DictationRecord;
  return !!x && typeof x.id === "string" && typeof x.startedAt === "number" && typeof x.text === "string"
    && Array.isArray(x.places) && ["live", "done", "cancelled"].includes(x.status);
}

export function parseHistory(raw: string | null): DictationRecord[] {
  try {
    const list = JSON.parse(raw ?? "[]");
    return Array.isArray(list) ? list.filter(isRecord) : [];
  } catch {
    return [];
  }
}
