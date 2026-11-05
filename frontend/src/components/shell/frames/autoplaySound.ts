// SOUND STARTS ONLY WHEN IT IS ASKED FOR.
//
// A player that starts by itself (the `autoplay` attribute) may show its
// picture but is never heard: a video playing that way is muted and keeps
// playing, an audio-only player is paused. Sound comes on when the player is
// unmuted, from its own controls or the page's speaker button, and stays on
// for that source until it changes. Applies to this document and to every
// same-origin app frame in it, so no app has to remember it. Playback started
// from code (a play button, an editor's timeline, a playlist moving on) is the
// app answering a press and is left alone, as is a live stream (`srcObject`),
// which is a call or a camera rather than a file.
//
// Outside pages are held to the same rule by their engines: the native page
// views ask WebKit for a user action before audio (`PageView.swift`), and an
// app running its own browser sets that browser's autoplay policy.

import { useEffect } from "react";
import { watchDocuments } from "../sameOriginFrames";

export interface PlayerState {
  autoplay: boolean;
  muted: boolean;
  volume: number;
  stream: boolean;
  /** Unmuted since its current source loaded, after being quieted. */
  allowed: boolean;
}

/** Whether a player that just started should be quieted. */
export function quietOnStart(p: PlayerState): boolean {
  return p.autoplay && !p.stream && !p.allowed && !p.muted && p.volume > 0;
}

const isMedia = (el: unknown): el is HTMLMediaElement =>
  !!el && typeof (el as HTMLMediaElement).pause === "function" && "autoplay" in (el as object);

/** The source each quieted player was quieted on, and whether it was unmuted since. */
const quieted = new WeakMap<HTMLMediaElement, { src: string; allowed: boolean }>();

function stateOf(m: HTMLMediaElement): PlayerState {
  const q = quieted.get(m);
  return {
    autoplay: m.autoplay,
    muted: m.muted,
    volume: m.volume,
    stream: m.srcObject != null,
    allowed: !!q && q.src === m.currentSrc && q.allowed,
  };
}

function quiet(m: HTMLMediaElement): void {
  if (!quietOnStart(stateOf(m))) return;
  quieted.set(m, { src: m.currentSrc, allowed: false });
  if (m.tagName === "AUDIO") m.pause();
  else m.muted = true;
}

function unmuted(m: HTMLMediaElement): void {
  const q = quieted.get(m);
  if (q && q.src === m.currentSrc && !m.muted && m.volume > 0) q.allowed = true;
}

/** Applies the rule to `doc`; call the result to stop. */
export function quietAutoplay(doc: Document): () => void {
  const onPlay = (e: Event) => { if (isMedia(e.target)) quiet(e.target); };
  const onVolume = (e: Event) => { if (isMedia(e.target)) unmuted(e.target); };
  doc.querySelectorAll("video, audio").forEach((m) => {
    if (isMedia(m) && !m.paused) quiet(m);
  });
  doc.addEventListener("play", onPlay, true);
  doc.addEventListener("volumechange", onVolume, true);
  return () => {
    doc.removeEventListener("play", onPlay, true);
    doc.removeEventListener("volumechange", onVolume, true);
  };
}

export function useQuietAutoplay(): void {
  useEffect(() => watchDocuments(quietAutoplay), []);
}
