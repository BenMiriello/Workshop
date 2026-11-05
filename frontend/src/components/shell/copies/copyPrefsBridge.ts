// Apps read and change what happens to Workshop copies (lib/copyPrefs.ts) by
// message, so a clipboard app can offer the choice in its own settings. Same
// `{v: 1, type}` shape as the other shell messages, from any same-origin frame:
//
//   app -> shell  workshop/v1/copyPrefs      ask
//   app -> shell  workshop/v1/setCopyPrefs   { from?, destination? }
//   shell -> app  workshop/v1/copyPrefs      { from, destination, canWatch, ok }
//
// `from` is where copies are saved from: "off", "workshop", or "everything"
// (also copies made in other apps, where the native app can watch them).

import { copyPrefs, type CopyDestination } from "../../../lib/copyPrefs";
import { nativeClipboard } from "../../../lib/nativeClipboard";

export const COPY_PREFS = "workshop/v1/copyPrefs";
export const SET_COPY_PREFS = "workshop/v1/setCopyPrefs";

export type SaveFrom = "off" | "workshop" | "everything";

export type CopyPrefsRequest =
  | { kind: "get" }
  | { kind: "set"; from?: SaveFrom; destination?: CopyDestination };

const FROM: readonly SaveFrom[] = ["off", "workshop", "everything"];
const DESTINATIONS: readonly CopyDestination[] = ["system", "workshop"];

export function parseCopyPrefsRequest(data: unknown): CopyPrefsRequest | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.v !== 1) return null;
  if (d.type === COPY_PREFS) return { kind: "get" };
  if (d.type !== SET_COPY_PREFS) return null;
  const req: CopyPrefsRequest = { kind: "set" };
  if (FROM.includes(d.from as SaveFrom)) req.from = d.from as SaveFrom;
  if (DESTINATIONS.includes(d.destination as CopyDestination)) req.destination = d.destination as CopyDestination;
  return req;
}

async function current(): Promise<{ from: SaveFrom; destination: CopyDestination; canWatch: boolean }> {
  const prefs = copyPrefs.get();
  const canWatch = nativeClipboard.canWatch();
  const watch = canWatch ? await nativeClipboard.getWatch() : null;
  const from: SaveFrom = !prefs.save ? "off" : watch === "everything" ? "everything" : "workshop";
  return { from, destination: prefs.destination, canWatch };
}

async function apply(req: Extract<CopyPrefsRequest, { kind: "set" }>): Promise<boolean> {
  let ok = true;
  const patch = {
    ...(req.from ? { save: req.from !== "off" } : {}),
    ...(req.destination ? { destination: req.destination } : {}),
  };
  if (Object.keys(patch).length) ok = await copyPrefs.set(patch);
  if (req.from && nativeClipboard.canWatch()) {
    try {
      await nativeClipboard.setWatch(req.from === "everything" ? "everything" : "workshop");
    } catch {
      ok = false;
    }
  }
  return ok;
}

/** Answer the copy-prefs messages of every same-origin frame. Returns the
 *  function that stops. */
export function listenForCopyPrefs(): () => void {
  const onMessage = (e: MessageEvent) => {
    if (e.origin !== window.location.origin || !e.source || e.source === window) return;
    const req = parseCopyPrefsRequest(e.data);
    if (!req) return;
    const to = e.source as Window;
    void (async () => {
      const ok = req.kind === "set" ? await apply(req) : true;
      to.postMessage({ v: 1, type: COPY_PREFS, ...(await current()), ok }, window.location.origin);
    })();
  };
  window.addEventListener("message", onMessage);
  return () => window.removeEventListener("message", onMessage);
}
