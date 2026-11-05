// Spoken read aloud: read the page in the middle of the screen, read the last
// reply of the chat words are going to, stop, and change the speed one step.
// The reader is the shell's one reader (reader.ts), so this reads exactly what
// a tap or a Read aloud button would.

import { composerFor, type ChatTarget } from "../../../lib/speech/chatTargets";
import type { NoticeHost } from "./actionRun";
import { readableAt } from "./readPlaces";
import { reader } from "./reader";
import { SPEEDS } from "./readerStore";

// The node under the middle of the screen, looking into a same-origin frame.
function nodeAtCentre(): Node | null {
  const x = innerWidth / 2;
  const y = innerHeight / 2;
  const el = document.elementFromPoint(x, y);
  if (el instanceof HTMLIFrameElement) {
    try {
      const r = el.getBoundingClientRect();
      return el.contentDocument?.elementFromPoint(x - r.left, y - r.top) ?? el;
    } catch { return el; }
  }
  return el;
}

export function readPage(host: NoticeHost): void {
  const node = nodeAtCentre();
  const r = node ? readableAt(node) : null;
  const text = r?.text().trim();
  if (!r || !text) { host.notice({ text: "Nothing to read here" }); return; }
  reader.read(text, r.source, r.el);
}

/** The last reply's own Read aloud button in the chat's view, which reads it
 *  and marks it as its button does. */
function lastReplyButton(chat: ChatTarget | null): HTMLElement | null {
  let scope: Element | null = chat ? composerFor(chat) : null;
  for (; scope; scope = scope.parentElement) {
    if (scope.querySelector(".chat-read")) break;
  }
  const buttons = (scope ?? document).querySelectorAll<HTMLElement>(".chat-read");
  return buttons[buttons.length - 1] ?? null;
}

export function readLastReply(host: NoticeHost, chat: ChatTarget | null): void {
  const b = lastReplyButton(chat);
  if (!b) { host.notice({ text: "No reply on screen to read" }); return; }
  if (b.getAttribute("aria-pressed") !== "true") b.click();
}

export function stopReading(): void {
  reader.stop();
}

export function readSpeed(host: NoticeHost, faster: boolean): void {
  const at = SPEEDS.indexOf(reader.getState().speed);
  const next = SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, (at < 0 ? 1 : at) + (faster ? 1 : -1)))];
  if (next === reader.getState().speed) { host.notice({ text: faster ? "Already the fastest" : "Already the slowest" }); return; }
  reader.setSpeed(next);
  host.notice({ text: `Reading at ${next}×` });
}
