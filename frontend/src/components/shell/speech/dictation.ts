// Live dictation for the whole shell: words go where dictation was started (the
// focused field, or the clipboard when none was), as they are said, revised
// there while they are still being said. That place changes only when the
// operator changes it: from the bar's menu, or "Dictate here" on another
// field. Focusing or typing in another field never moves the words.
//
// One controller, held as a small external store so the chip, the bar and the
// shortcuts read the same state. The words half (where they go now, the
// record of where they went, undo and moves) is dictationParts.ts; how they get
// there, and when they become the operator's, is dictationWords.ts; spoken
// commands, voiceCommands.ts. It keeps listening across pages to Stop.

import { MicError } from "../../../lib/voice/errors";
import { currentMicClient, ensureSystemMic, micBlockedHelp } from "../../../lib/speech/micAccess";
import type { SpeechMode } from "../../../lib/speech/commandsApi";
import { speechPrefs } from "../../../lib/speech/prefs";
import { IDLE, messageOf, type DictationState } from "./dictationState";
import { CLIPBOARD, destinationFor, recentOf, samePlace, type Destination } from "./destination";
import { DictationParts } from "./dictationParts";
import { bringNotice, lastPieceIn } from "./pieceMoves";
import { DictationMode } from "./dictationMode";
import { LiveSession } from "./liveSession";
import { phraseHistory } from "./phraseHistory";
import { sessionEvents } from "./sessionEvents";
import { hereOf, pressMoves } from "./fieldSwitch";
import { BEAT_MS, elsewhere } from "./elsewhere";
import { copyAtStop, endsOnItsOwn, keepOn, rememberDestination } from "./stopping";
import { runCommand } from "./voiceCommands";

class Dictation extends DictationParts {
  private session: LiveSession | null = null;
  private spokeAt = 0;
  private watch: ReturnType<typeof setInterval> | null = null;
  private numbers = false;
  private beatAt = 0;
  private modes = new DictationMode({
    state: () => this.state,
    set: (patch) => this.set(patch),
    send: (mode) => this.session?.setMode(mode),
    freeze: () => this.words.freeze(),
    chooseDestination: (d) => this.chooseDestination(d),
  });

  protected commit(to: number): void { this.session?.commit(to); }

  private fail(message: string, blocked = false) { this.teardown(); this.showError(message, blocked); }

  private teardown() {
    this.session?.cancel();
    this.session = null;
    this.words.stop();
    if (this.watch) { clearInterval(this.watch); this.watch = null; }
    elsewhere.off();
  }

  /** Start listening into the field `el`, or `into`, or the clipboard. */
  async start(el: Element | null, into?: Destination): Promise<void> {
    if (this.busy) return;
    this.clearErrorTimer();
    elsewhere.claim();
    const dest = destinationFor(el) ?? into ?? CLIPBOARD;
    this.spokeAt = Date.now();
    this.transcript = this.newTranscript();
    this.words = this.newWords();
    this.set({
      ...IDLE, phase: "starting", into: dest, startedAt: Date.now(),
      originEl: dest.kind === "field" ? dest.target?.el ?? null : null,
    });
    rememberDestination(dest);
    this.modes.load();
    if (!(await ensureSystemMic())) {
      this.fail(micBlockedHelp(currentMicClient()), true);
      return;
    }
    if (this.state.phase !== "starting") return;
    const session: LiveSession = new LiveSession(this.transcript.id, sessionEvents({
      current: () => this.session === session,
      words: this.words,
      spoke: () => { this.spokeAt = Date.now(); },
      state: () => this.state,
      set: (patch) => this.set(patch),
      command: (m) => runCommand(this, m),
      paused: (paused) => this.paused(paused),
      mode: (mode, said) => this.modes.heard(mode, said),
      stop: () => { void this.stop(); },
      unwritten: (text) => this.keepUnwritten(text),
      taken: () => void this.stop().then(() => this.notice({ text: "Dictation moved to another window or device" })),
    }));
    this.session = session;
    session.setNumbers(this.numbers);
    try {
      await session.start();
    } catch (e) {
      if (this.session === session) this.fail(messageOf(e), e instanceof MicError && e.kind === "notAllowed");
      return;
    }
    if (this.session !== session) return;
    this.set({ phase: "listening" });
    this.watch = setInterval(() => this.check(), 1000);
  }

  // Listening ending on its own (stopping.ts). A server never reached still
  // turns what was said into text, then says plainly that it could not
  // connect. The check also looks again for a field that left the page.
  private check(): void {
    if (!this.listening) return;
    const s = this.state;
    if (s.waiting) this.words.render();
    if (Date.now() - this.beatAt >= BEAT_MS) this.beat(s);
    const end = endsOnItsOwn(s, this.spokeAt, speechPrefs.get().keepOn);
    if (end === "silence") void this.stop();
    else if (end === "unreached") {
      void this.stop().then(() => {
        this.set({ originEl: s.originEl });
        this.fail("Dictation could not connect to the speech server.");
      });
    }
  }

  // Other windows show where this one's words are going; "Keep dictation on"
  // knows this tab is still the one dictating.
  private beat(s: DictationState): void {
    this.beatAt = Date.now();
    const d = s.into ?? CLIPBOARD;
    elsewhere.on(d, recentOf(d), s.said.trim() ? s.said.trim().split(/\s+/).length : 0);
    keepOn.beat();
  }

  /** A press somewhere in the shell or a frame (fieldSwitch.ts). */
  pressed(target: Node | null): void {
    if (!this.busy) return;
    this.words.pressed(target);
    if (pressMoves(this.state.here, target, this.spokeAt)) this.dictateHere();
  }

  /** The focused element changed somewhere in the shell or a frame. */
  focus(el: Element | null): void {
    const here = this.busy ? hereOf(el, this.state.into) : null;
    if (this.busy && !samePlace(here, this.state.here)) this.set({ here });
  }

  /** Words said from now on go to `d`; what was placed stays where it is.
   *  Words held for a field given up on are unplaced: copied at Stop, and
   *  still in the pieces to move. */
  setDestination(d: Destination): void {
    if (samePlace(d, this.state.into)) return;
    this.words.freeze();
    if (this.words.waiting) {
      this.words.clip = [this.words.clip, this.words.waiting].filter(Boolean).join(" ");
      this.words.waiting = "";
      if (this.state.into) this.transcript?.mark(this.state.into, "waiting", "unplaced");
    }
    this.transcript?.cut();
    this.set({ into: d, here: null, waiting: false, originEl: d.kind === "field" ? d.target?.el ?? null : null });
    rememberDestination(d);
    this.words.render();
  }

  /** The operator picked where words go next. Picking a place is asking to
   *  dictate into it, so a paused dictation carries on. */
  chooseDestination(d: Destination): void {
    const was = this.state.into;
    const moved = !samePlace(d, was);
    this.setDestination(d);
    if (this.state.paused) this.pause(false);
    const last = moved && was ? lastPieceIn(this.state.pieces, was) : null;
    if (last) this.notice(bringNotice(d, () => this.movePiece(last, d), (n) => this.notice(n)));
  }

  dictateHere(): void { if (this.state.here) this.chooseDestination(this.state.here); }

  /** A key was typed in `el`: the words in it are the operator's now. */
  typed(el: Element | null): void {
    if (this.busy) { this.words.typed(el); this.transcript?.cut(); }
    else phraseHistory.typedIn(el);
  }

  /** How spoken commands are heard from now on, saved for the next dictation. */
  setMode(mode: SpeechMode): void { this.modes.choose(mode); }

  /** Numbered things are on screen (numbered.ts): a number said alone picks. */
  setNumbers(on: boolean): void { this.numbers = on; this.session?.setNumbers(on); }

  pause(paused: boolean): void {
    if (!this.listening) return;
    this.session?.pause(paused);
    this.paused(paused);
  }

  private paused(paused: boolean): void {
    if (paused) this.words.freeze();
    if (paused !== this.state.paused) this.set({ paused, open: paused ? "" : this.state.open });
  }

  /** Stop listening; every word said is settled and delivered. `gesture`: a
   *  click or key started this, so the system clipboard can be written. */
  async stop(gesture = false): Promise<void> {
    if (!this.listening) return;
    const session = this.session;
    // Words a field never took are copied, as the clipboard's are.
    const copy = copyAtStop(this.state.into?.kind === "clipboard" || !!this.words.waiting || !!this.words.clip, gesture);
    if (this.watch) { clearInterval(this.watch); this.watch = null; }
    this.set({ phase: "finishing", level: 0 });
    await session?.stop();
    this.words.freeze();
    this.words.render();
    const held = this.words.waiting;
    this.words.waiting = "";
    this.words.stop();
    this.session = null;
    if (held && this.state.into) this.transcript?.mark(this.state.into, "waiting", "unplaced");
    this.transcript?.close("done");
    this.transcript = null;
    keepOn.stopped();
    elsewhere.off();
    this.set({ ...IDLE, notice: this.state.notice });
    await copy.done(this.words.clip, held, (n) => this.notice(n));
  }

  retry(): void { const el = this.state.originEl; this.dismiss(); void this.start(el); }

  /** The shortcut and the chip: start when idle, stop while listening. */
  toggle(el: Element | null, gesture = true): void {
    if (this.listening) void this.stop(gesture);
    else if (!this.busy) void this.start(el);
  }
}

export const dictation = new Dictation();
elsewhere.onStop(() => void dictation.stop());
export type CommandHost = Pick<Dictation,
  "write" | "notice" | "undo" | "putBack" | "takeOut" | "pause" | "stop" | "setDestination" | "setMode" | "sentFrom"
  | "movePiece" | "keepUnwritten">
  & { getState: () => DictationState };
export type { DictationPhase, DictationState } from "./dictationState";
