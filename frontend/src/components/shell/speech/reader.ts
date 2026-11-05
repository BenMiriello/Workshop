// Read text aloud, one sentence per request, the next fetched while the
// current one plays.
//
// One reader for the whole shell, as an external store: starting a new read
// stops the one before it, and the floating player and every Read aloud button
// see the same state, including which sentence is being read. Speed is applied
// at playback (pitch kept), so a change takes effect at once.
//
// When the service cannot speak, or cannot be reached, the browser's own
// speech synthesis reads instead, and a sentence the service fails on mid-read
// hands it and the rest to the browser too. iOS only lets that engine start
// inside a gesture, so a service read primes it from the click that started
// reading (readerAudio.ts). The voice is the one chosen in Settings when the
// read starts; the browser reads in its own.
//
// Pausing the service's audio holds its place in the sentence; pausing browser
// speech (no pause works everywhere) stops it, and resuming reads it again.

import { speechApi } from "../../../lib/speech/api";
import { speechPrefs } from "../../../lib/speech/prefs";
import { skipTarget } from "../../../lib/speech/readCursor";
import { readPieces } from "../../../lib/speech/sentences";
import { playClip, primeBrowserSpeech, speakSentence, unlockAudio, type Playback } from "./readerAudio";
import { Passage, type Point } from "./passage";
import { ServiceClips } from "./readerClips";
import { IDLE, SPEEDS, Store, storedSpeed, storeSpeed, type ReaderState } from "./readerStore";

export { SPEEDS };
export type { ReaderPhase, ReaderState } from "./readerStore";

type Engine = "service" | "browser";
const ERROR_SHOWN_MS = 5000;

const hasBrowserSpeech = () => typeof speechSynthesis !== "undefined";
const messageOf = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

class Reader extends Store<ReaderState> {
  /** Bumped by every read and stop. */
  private session = 0;
  /** Bumped by every move within a read: a sentence still sounding under an
   *  older step is stale and ignored. */
  private step = 0;
  private pieces: string[] = [];
  /** Where the read is on the page; null when it has no place there. */
  passage: Passage | null = null;
  private engine: Engine | null = null;
  private audio: HTMLAudioElement | null = null;
  private clips: ServiceClips | null = null;
  private playback: Playback | null = null;
  /** The element holds the current sentence's clip. */
  private loaded = false;
  /** When browser speech started the current sentence; 0 when it has not. */
  private startedAt = 0;
  private errorTimer: ReturnType<typeof setTimeout> | null = null;
  /** The last health answer for speech output; null until one arrives. */
  private serviceSpeaks: boolean | null = null;

  /** Ask the service whether it can speak, ahead of the first read, so the
   *  choice of engine can be made inside the click that starts reading. */
  probe(): void {
    void speechApi.health().then((h) => { this.serviceSpeaks = !!h?.tts.available; });
  }

  get active(): boolean {
    const { phase } = this.state;
    return phase === "loading" || phase === "playing" || phase === "paused";
  }

  /** Start reading `text`. Call from the click or key press itself: iOS only
   *  lets a page play sound it started inside a gesture, so the audio element
   *  (or the speech engine) is started synchronously here. `place` is where
   *  the text is on the page, if it is, and `from` a point in it to start at. */
  read(text: string, source: string, place?: Element | Range | null, from?: Point | null): void {
    this.stop();
    const pieces = readPieces(text);
    if (!pieces.length) return;
    const session = this.session;
    this.pieces = pieces;
    this.passage = place ? Passage.over(place, pieces) : null;
    this.passage?.watch();
    const index = (from && this.passage?.indexAt(from)) || 0;
    if (this.errorTimer) { clearTimeout(this.errorTimer); this.errorTimer = null; }
    this.set({ phase: "loading", source, error: null, index, count: pieces.length, placed: !!this.passage });
    const browser = hasBrowserSpeech();
    if (this.serviceSpeaks === false && browser) { this.begin("browser"); return; }
    this.audio = unlockAudio(this.audio);
    if (browser) primeBrowserSpeech();
    this.clips = new ServiceClips(pieces, speechPrefs.get().voice ?? undefined);
    void speechApi.health().then((h) => {
      if (this.session !== session) return;
      this.serviceSpeaks = !!h?.tts.available;
      if (this.serviceSpeaks) this.begin("service");
      else if (browser) this.begin("browser");
      else this.fail(h?.tts.error || "Read aloud is not available right now.");
    });
  }

  stop(): void {
    this.session++;
    this.halt();
    this.clips?.cancel();
    this.clips = null;
    this.engine = null;
    this.pieces = [];
    this.passage?.close();
    this.passage = null;
    if (this.audio) this.audio.removeAttribute("src");
    if (hasBrowserSpeech()) speechSynthesis.cancel();
    if (this.state.phase !== "idle") this.set({ ...IDLE, error: null });
  }

  pause(): void {
    if (this.state.phase !== "loading" && this.state.phase !== "playing") return;
    this.set({ phase: "paused" });
    if (this.engine === "service") this.audio?.pause();
    else if (this.engine === "browser") this.halt();
  }

  /** Call from the click itself, as for `read`. */
  resume(): void {
    if (this.state.phase !== "paused") return;
    if (this.engine === "service" && this.loaded && this.audio) {
      const step = this.step;
      this.set({ phase: "playing" });
      this.audio.play().catch(() => {
        if (this.step === step && this.state.phase === "playing") this.fail("Playback was blocked. Press play again.");
      });
    } else {
      this.go(this.state.index);
    }
  }

  /** Move `delta` sentences. Going back while a moment into a sentence
   *  restarts it instead. Plays from there, paused or not. */
  skip(delta: number): void {
    if (!this.active) return;
    const target = skipTarget(this.state.index, this.state.count, delta, this.elapsed());
    if (target !== null) this.go(target);
  }

  /** Read on from sentence `index`, paused or not. Call from the tap itself. */
  jump(index: number): void {
    if (this.active && index >= 0 && index < this.state.count) this.go(index);
  }

  setSpeed(speed: number): void {
    if (!SPEEDS.includes(speed)) return;
    storeSpeed(speed);
    if (this.audio) this.audio.playbackRate = speed;
    this.set({ speed });
  }

  private fail(message: string) {
    this.stop();
    this.set({ phase: "error", error: message });
    const session = this.session;
    this.errorTimer = setTimeout(() => {
      if (this.session === session && this.state.phase === "error") this.set({ phase: "idle", error: null });
    }, ERROR_SHOWN_MS);
  }

  /** Silence whatever is sounding and make its callbacks stale. */
  private halt() {
    this.step++;
    this.playback?.interrupt();
    this.playback = null;
    this.loaded = false;
    this.startedAt = 0;
    this.audio?.pause();
    if (this.engine === "browser") speechSynthesis.cancel();
  }

  private elapsed(): number {
    if (this.engine === "service") return this.loaded && this.audio ? this.audio.currentTime / this.state.speed : 0;
    return this.startedAt ? (performance.now() - this.startedAt) / 1000 : 0;
  }

  private begin(engine: Engine) { this.engine = engine; this.playAt(this.state.index, this.step); }

  private go(index: number) {
    this.halt();
    this.set({ index, phase: this.state.phase === "paused" ? "loading" : this.state.phase });
    if (this.engine) this.playAt(index, this.step);
  }

  private playAt(i: number, step: number) {
    if (this.engine === "service") void this.serviceAt(i, step);
    else if (this.engine === "browser") this.browserAt(i, step);
  }

  private advance(i: number, step: number) {
    if (i + 1 >= this.pieces.length) { this.stop(); return; }
    this.set({ index: i + 1 });
    this.playAt(i + 1, step);
  }

  private async serviceAt(i: number, step: number) {
    this.loaded = false;
    const clips = this.clips;
    if (!clips) return;
    if (!clips.has(i) && this.state.phase === "playing") this.set({ phase: "loading" });
    let blob: Blob;
    try {
      blob = await clips.get(i);
    } catch (e) {
      if (this.step !== step) return;
      if (!hasBrowserSpeech()) { this.fail(messageOf(e, "Read aloud failed.")); return; }
      clips.cancel();
      this.clips = null;
      this.engine = "browser";
      this.browserAt(i, step);
      return;
    }
    const audio = this.audio;
    if (this.step !== step || !audio) return;
    if (i + 1 < this.pieces.length) void clips.get(i + 1);
    const url = URL.createObjectURL(blob);
    const held = this.state.phase === "paused";
    const playback = playClip(audio, url, this.state.speed, held, () => this.state.phase === "paused");
    this.playback = playback;
    this.loaded = true;
    if (!held && this.state.phase !== "playing") this.set({ phase: "playing" });
    try {
      await playback.done;
    } catch (e) {
      if (this.step === step) this.fail(messageOf(e, "Read aloud failed."));
      return;
    } finally {
      URL.revokeObjectURL(url);
      if (this.playback === playback) this.playback = null;
    }
    if (this.step === step) this.advance(i, step);
  }

  private browserAt(i: number, step: number) {
    this.startedAt = 0;
    if (this.state.phase === "paused") return;
    speakSentence(this.pieces[i], this.state.speed, {
      started: () => {
        if (this.step !== step) return;
        this.startedAt = performance.now();
        if (this.state.phase !== "playing") this.set({ phase: "playing" });
      },
      ended: () => { if (this.step === step) this.advance(i, step); },
      failed: () => { if (this.step === step) this.fail("The browser could not read this aloud."); },
    });
  }
}

export const reader = new Reader({ ...IDLE, speed: storedSpeed(), error: null });
