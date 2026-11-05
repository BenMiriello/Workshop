// The sound side of the reader: unlocking the audio element and the browser's
// speech engine inside the gesture that starts a read, and playing one fetched
// sentence on the element.
//
// iOS only lets a page play sound it started inside a gesture. Once the audio
// element has played from one, later plays on that same element are allowed,
// so the reader keeps one element for good. Speech synthesis is primed the
// same way, with a silent utterance, so a mid-read handoff to it is heard.

import { encodeWav } from "../../../lib/speech/wav";

let silence: string | null = null;
function silentClip(): string {
  silence ??= URL.createObjectURL(new Blob([encodeWav([new Int16Array(240)], 24000)], { type: "audio/wav" }));
  return silence;
}

export function primeBrowserSpeech(): void {
  const u = new SpeechSynthesisUtterance(" ");
  u.volume = 0;
  speechSynthesis.speak(u);
}

/** Play silence on `audio` (made if there is none yet). Call inside the
 *  gesture. */
export function unlockAudio(audio: HTMLAudioElement | null): HTMLAudioElement {
  const el = audio ?? new Audio();
  el.preservesPitch = true;
  el.src = silentClip();
  el.play().catch(() => {});
  return el;
}

export type Playback = {
  /** Settles when the clip ends (or is interrupted) and fails if it cannot play. */
  done: Promise<void>;
  /** End it now, settling `done` as if it had ended. */
  interrupt(): void;
};

/** Load `url` on `audio` and, unless `held`, start it. A held clip waits for
 *  the caller's own `audio.play()`. A play the caller paused is not a failure:
 *  `isPaused` is asked before one is reported. */
export function playClip(
  audio: HTMLAudioElement, url: string, speed: number, held: boolean, isPaused: () => boolean,
): Playback {
  let interrupt = () => {};
  const done = new Promise<void>((resolve, reject) => {
    let settled = false;
    const settle = (fn: () => void) => () => {
      if (settled) return;
      settled = true;
      audio.onended = null;
      audio.onerror = null;
      fn();
    };
    audio.onended = settle(resolve);
    audio.onerror = settle(() => reject(new Error("This browser could not play the audio.")));
    interrupt = settle(resolve);
    audio.src = url;
    audio.playbackRate = speed;
    if (held) return;
    audio.play().catch(() => {
      if (!isPaused()) settle(() => reject(new Error("Playback was blocked. Press Read aloud again.")))();
    });
  });
  return { done, interrupt: () => interrupt() };
}

/** Speak one sentence with the browser's engine. A sentence cut short by
 *  `speechSynthesis.cancel()` is not a failure. */
export function speakSentence(
  text: string, rate: number, on: { started(): void; ended(): void; failed(): void },
): void {
  const u = new SpeechSynthesisUtterance(text);
  u.rate = rate;
  u.onstart = () => on.started();
  u.onend = () => on.ended();
  u.onerror = (e) => {
    if (e.error !== "interrupted" && e.error !== "canceled") on.failed();
  };
  speechSynthesis.speak(u);
}
