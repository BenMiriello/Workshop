// When a dictation ends on its own, and what is kept about one beyond the
// page: the system clipboard for a dictation into the clipboard at Stop, the
// recent places words went, and that dictation was on, for "Keep dictation
// on".
//
// A browser lets a page write the clipboard only inside a click or key press,
// and the last words arrive after Stop is pressed. So the write is started in
// the press with a promise of the text (a ClipboardItem whose value settles
// later, which Safari and Chrome accept); an empty dictation rejects it, which
// leaves what the operator last copied alone. The Mac and iPhone apps write
// through their own bridge, with no press needed. The write is not reported as
// a copy: the dictation itself is published, with its recording. While copies
// stay in the Workshop the system clipboard is left alone and the words are
// what a paste in the Workshop gives.

import { copyPrefs } from "../../../lib/copyPrefs";
import { nativeClipboard } from "../../../lib/nativeClipboard";
import { unreported } from "../copies/clipboardWrites";
import { heldCopy } from "../copies/heldCopy";
import { speechPrefs } from "../../../lib/speech/prefs";
import { parseRecent, recentPlaces, type RecentPlace } from "../../../lib/speech/recentPlaces";
import { userStorage } from "../../../lib/storage";
import { CLIPBOARD, fromRecent, recentOf, type Destination } from "./destination";
import { TAB } from "./elsewhere";
import { wordCount } from "./dictationState";
import type { NoticeInput } from "./notices";

// A forgotten microphone does not stay open: this long with nothing said stops
// it, unless dictation is set to stay on.
export const SILENCE_STOP_MS = 5 * 60 * 1000;
const CONNECT_GIVE_UP_MS = 10_000;

/** Why listening ends on its own now, if it does: a long silence (unless
 *  dictation stays on), or a server never reached since `startedAt`. */
export function endsOnItsOwn(
  s: { everConnected: boolean; startedAt: number | null }, spokeAt: number, keepOn: boolean, now = Date.now(),
): "silence" | "unreached" | null {
  if (!keepOn && now - spokeAt > SILENCE_STOP_MS) return "silence";
  if (!s.everConnected && s.startedAt !== null && now - s.startedAt > CONNECT_GIVE_UP_MS) return "unreached";
  return null;
}

/** True once the dictation's text is on the system clipboard. */
export function finishClipboard(text: Promise<string>, gesture: boolean): Promise<boolean> {
  if (copyPrefs.held()) {
    return text.then((t) => {
      if (t) heldCopy.took(t);
      return !!t;
    });
  }
  if (nativeClipboard.available()) return text.then((t) => (t ? nativeClipboard.write(t) : false));
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (!gesture || typeof ClipboardItem === "undefined" || !clip?.write) return Promise.resolve(false);
  try {
    const blob = text.then((t) => (t ? new Blob([t], { type: "text/plain" }) : Promise.reject(new Error("empty"))));
    const item = new ClipboardItem({ "text/plain": blob });
    return unreported(() => clip.write([item])).then(() => true, () => false);
  } catch {
    return Promise.resolve(false);
  }
}

/** At Stop: what was said to the clipboard, and words a field never took,
 *  copied, and the bar told how it went. Made before anything is awaited, or
 *  a browser no longer counts the gesture. */
export function copyAtStop(copyNow: boolean, gesture: boolean) {
  let finished: (text: string) => void = () => {};
  const copied = copyNow ? finishClipboard(new Promise<string>((r) => { finished = r; }), gesture) : null;
  return {
    async done(clip: string, held: string, notice: (n: NoticeInput) => void): Promise<void> {
      const text = [clip, held].filter(Boolean).join(" ");
      finished(text);
      if (!text || !(copied || held)) return;
      const ok = copied ? await copied : false;
      notice(ok ? { text: `Copied ${wordCount(text)}` } : { text: wordCount(text), copy: text });
    },
  };
}

const ON_KEY = "speech.on";
const ON_KEY_TAB = "speech.on.tab";
// A tab that has said nothing for this long is gone: another may start again.
const TAB_GONE_MS = 15_000;

type OnPlace = RecentPlace | { kind: "clipboard" };
type OnTab = { tab: string; beat: number };

function onTab(): OnTab | null {
  try {
    const t = JSON.parse(userStorage.local.getItem(ON_KEY_TAB) ?? "null") as OnTab | null;
    return t && typeof t.tab === "string" && typeof t.beat === "number" ? t : null;
  } catch {
    return null;
  }
}

/** "Keep dictation on": dictation starts again after a reload, in the tab that
 *  had it (or in any one, once that tab has been gone a while), never in every
 *  window that opens. */
export const keepOn = {
  started(d: Destination): void {
    if (!speechPrefs.get().keepOn) return;
    const place: OnPlace | null = d.kind === "clipboard" ? { kind: "clipboard" } : recentOf(d);
    userStorage.local.setItem(ON_KEY, JSON.stringify(place ?? { kind: "clipboard" }));
    this.beat();
  },
  beat(): void {
    if (speechPrefs.get().keepOn) userStorage.local.setItem(ON_KEY_TAB, JSON.stringify({ tab: TAB, beat: Date.now() }));
  },
  /** Dictation stopped here; another window that took over keeps its own. */
  stopped(): void {
    const t = onTab();
    if (t && t.tab !== TAB) return;
    userStorage.local.removeItem(ON_KEY);
    userStorage.local.removeItem(ON_KEY_TAB);
  },
  /** Where dictation was going when the page went away while it was on. */
  resumeInto(): Destination | null {
    if (!speechPrefs.get().keepOn) return null;
    const raw = userStorage.local.getItem(ON_KEY);
    if (!raw) return null;
    const t = onTab();
    if (t && t.tab !== TAB && Date.now() - t.beat < TAB_GONE_MS) return null;
    try {
      if ((JSON.parse(raw) as OnPlace).kind === "clipboard") return CLIPBOARD;
    } catch {
      return null;
    }
    const [place] = parseRecent(`[${raw}]`);
    return place ? fromRecent(place) : null;
  },
};

/** Words go to `d` now: remember it among the recent places, and as where a
 *  dictation kept on resumes. */
export function rememberDestination(d: Destination): void {
  const r = recentOf(d);
  if (r) recentPlaces.add(r);
  keepOn.started(d);
}
