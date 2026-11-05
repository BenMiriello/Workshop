// Where a live dictation's words go as the server hears them.
//
// The server sends the OPEN STRETCH (everything said since the last settled
// point) whole, again and again as it is revised, and a FINAL when part of it
// settles. Both name the stream sample they start at, `from`; anything not
// starting where this side is now (`openFrom`) belongs to text already let go
// and is ignored. A spoken command arrives with its words already cut out.
//
// Words go to a SINK, asked for again as they arrive:
//   live   a field on screen (or a chat's composer that is): the open stretch
//          is shown in it (LiveRegion) and rewritten there in place;
//   draft  a chat that is not open: words go into its draft as said (draftTail);
//   clip   the clipboard: settled words collect here, for the bar and Stop;
//   wait   a field that left the page: settled words wait for it to return
//          and be pressed in (fieldReturn.ts).
// Words not shown in a field are reported to the bar, which shows the newest.
//
// When the operator takes a field back (types in it, moves the caret, sends
// it) the region is FROZEN: its words stay as they are, and the server is told
// to start the next stretch after them, so nothing is typed twice. A hidden
// helper field (a terminal's input) cannot take text back, so it is sent only
// words that have stopped changing.

import { appendToDraft, type ChatTarget } from "../../../lib/speech/chatTargets";
import { appendPiece } from "../../../lib/speech/insertText";
import type { LiveCommand, LiveFinal } from "../../../lib/speech/liveWire";
import { sentencesOf, type Placed } from "../../../lib/speech/phraseUndo";
import { insertAtTarget, unseen, type DictationTarget } from "./insertAtTarget";
import { FieldReturn } from "./fieldReturn";
import { DraftTail } from "./draftTail";
import { LiveRegion } from "./liveRegion";
import type { PieceState } from "../../../lib/speech/pieces";
import { draftEntry, heldEntry, phraseHistory, valueEntry, type PhraseEntry } from "./phraseHistory";

const TYPING_QUIET_MS = 900;
const HELPER_STILL_MS = 1200;

export type Sink =
  | { kind: "live"; target: DictationTarget; name: string }
  | { kind: "draft"; chat: ChatTarget; name: string }
  | { kind: "clip"; name: string }
  | { kind: "wait"; name: string };

export type WordHooks = {
  into: () => Sink;
  /** The text shown through `to` is the operator's now. */
  commit: (to: number) => void;
  /** `open`: words heard but not in a field on screen (the bar shows the
   *  newest); `pending`: every word still being said, for the count; `kept`:
   *  what the clipboard or a waiting field holds now, null elsewhere. */
  report: (open: string, pending: string, kept: string | null) => void;
  /** Settled words went where words go now, in `state`. */
  placed: (text: string, state: PieceState) => void;
  /** Words held for a field that left the page went into it. */
  tookWaiting: () => void;
  /** The place words go now (destination.ts placeKey), for undo. */
  key: () => string;
};

type ValueField = HTMLInputElement | HTMLTextAreaElement;

export class DictationWords {
  openFrom = 0;
  /** What this dictation put in the clipboard. */
  clip = "";
  /** Words waiting for a field that left the page. */
  waiting = "";
  private region: LiveRegion | null = null;
  private shownTo = 0;
  private latest: { text: string; to: number } | null = null;
  private typedAt = 0;
  private returning = new FieldReturn();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private tail: DraftTail;

  constructor(private hooks: WordHooks) {
    const letGo = (to: number) => { this.openFrom = to; this.latest = null; hooks.commit(to); };
    this.tail = new DraftTail({ placed: (text) => hooks.placed(text, "placed"), letGo });
  }

  private sinkNow = (): Sink => this.returning.route(this.hooks.into(), !!this.waiting);

  /** The operator pressed at `target`: in a field words wait for, they go in. */
  pressed(target: Node | null): void {
    this.returning.pressed(target);
    setTimeout(() => this.render(), 0);
  }

  /** The open stretch as heard now. */
  text(text: string, from: number, to: number): void {
    if (from !== this.openFrom) return;
    this.latest = { text, to };
    this.render();
  }

  /** Part of the stretch settled. */
  final(m: LiveFinal): void {
    if (m.from !== this.openFrom) return;
    this.openFrom = m.to;
    this.latest = m.rest ? { text: m.rest, to: m.restTo } : null;
    const sink = this.sinkNow();
    if (sink.kind === "draft" && this.tail.holds(sink.chat)) {
      this.tail.settle(sink.chat, sink.name, m);
      return this.render();
    }
    if (sink.kind !== "live") {
      if (m.text) this.deliver(sink, m.text);
      return this.render();
    }
    if (!this.region && !unseen(sink.target.el)) this.openIn(sink);
    if (this.region) {
      const el = this.region.target.el as ValueField;
      if (this.region.settle(m.text, m.rest, (p) => this.undoable(el, p))) {
        if (m.text) this.hooks.placed(m.text, "placed");
        this.shownTo = m.rest ? m.restTo : m.to;
        if (!m.rest) this.region = null;
      } else {
        // The field changed under the region: what it showed is the
        // operator's, and the server says the words after it again.
        this.openFrom = this.shownTo;
        this.latest = null;
        this.hooks.commit(this.shownTo);
        this.region = null;
      }
    } else if (m.text && insertAtTarget(sink.target, m.text)) {
      this.hooks.placed(m.text, "placed");
    }
    this.render();
  }

  /** A spoken command: its words are not text, and what follows starts after
   *  them. */
  command(m: LiveCommand): void {
    if (m.to <= this.openFrom) return;
    this.freeze();
    this.openFrom = m.to;
    this.latest = null;
    this.render();
  }

  /** Words to write as if they had been said and settled now: a command that
   *  did not apply, or a command's own text. */
  write(text: string): void {
    this.freeze();
    const sink = this.sinkNow();
    if (sink.kind !== "live") return this.deliver(sink, text);
    const f = sink.target.el as ValueField;
    const before = sink.target.kind === "value" ? f.value : null;
    if (!insertAtTarget(sink.target, text)) return;
    this.hooks.placed(text, "placed");
    if (before === null) return;
    let start = 0;
    while (start < before.length && before[start] === f.value[start]) start++;
    const added = f.value.slice(start, start + f.value.length - before.length);
    if (added) this.undoable(f, { start, text: added });
  }

  private undoable(f: ValueField, p: Placed): void {
    for (const s of sentencesOf(p)) this.keep(valueEntry(f, s));
  }

  private keep(e: PhraseEntry): void { phraseHistory.placed({ ...e, key: this.hooks.key() }); }

  /** A key was typed in `el`: the words there are the operator's now. */
  typed(el: Element | null): void {
    this.typedAt = Date.now();
    phraseHistory.typedIn(el);
    this.freeze();
    this.later(TYPING_QUIET_MS);
  }

  /** The destination may have changed, or a field may have come back. */
  render(): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    const sink = this.sinkNow();
    if (this.region && (sink.kind !== "live" || this.region.target.el !== sink.target.el)) this.freeze();
    if (this.tail.active && (sink.kind !== "draft" || !this.tail.holds(sink.chat))) this.tail.freeze();
    // A field that left the page is back and the operator is in it: what
    // waited for it goes in at its end.
    if (sink.kind === "live" && this.waiting) {
      this.returning.putIn(sink.target, this.waiting);
      this.waiting = "";
      this.hooks.tookWaiting();
    }
    const latest = this.latest;
    if (sink.kind === "draft" && latest) this.tail.show(sink.chat, sink.name, () => this.latest);
    if (!latest || sink.kind !== "live") return this.report(sink);
    const quiet = Date.now() - this.typedAt;
    if (!this.region && quiet < TYPING_QUIET_MS) return this.later(TYPING_QUIET_MS - quiet);
    if (unseen(sink.target.el)) return this.helper(sink, latest);
    if (!this.region) this.openIn(sink);
    if (this.region!.show(latest.text)) this.shownTo = latest.to;
    else this.freeze();
    this.report(sink);
  }

  /** Let go of whatever is shown; it stays where it is. */
  freeze(): void {
    const region = this.region;
    this.region = null;
    if (!region?.text) return;
    const placed = region.placed;
    if (placed) this.undoable(region.target.el as ValueField, placed);
    this.hooks.placed(region.text, "placed");
    this.openFrom = this.shownTo;
    this.latest = null;
    this.hooks.commit(this.shownTo);
  }

  /** Words heard but in no field yet. */
  get unplaced(): string {
    return this.region ? "" : this.latest?.text ?? "";
  }

  stop(): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    this.returning.reset();
    this.tail.cancel();
    this.region = null;
  }

  // Settled words for a place that is not a field on screen.
  private deliver(sink: Exclude<Sink, { kind: "live" }>, text: string): void {
    const phrases = sentencesOf({ start: 0, text });
    if (sink.kind === "draft") {
      appendToDraft(sink.chat, text);
      for (const s of phrases) this.keep(draftEntry(sink.chat, s.text));
    } else {
      const key = sink.kind === "clip" ? "clip" : "waiting";
      this[key] = appendPiece(this[key], text);
      const get = () => this[key];
      const set = (v: string) => { this[key] = v; };
      for (const s of phrases) this.keep(heldEntry(get, set, s.text));
    }
    this.hooks.placed(text, sink.kind === "draft" ? "placed" : sink.kind === "clip" ? "clipboard" : "waiting");
  }

  private openIn(sink: Extract<Sink, { kind: "live" }>): void {
    this.region = new LiveRegion(sink.target);
  }

  private helper(sink: Extract<Sink, { kind: "live" }>, latest: { text: string; to: number }): void {
    this.timer = setTimeout(() => {
      if (this.latest !== latest) return;
      this.openFrom = latest.to;
      this.latest = null;
      this.hooks.commit(latest.to);
      if (insertAtTarget(sink.target, latest.text)) this.hooks.placed(latest.text, "placed");
    }, HELPER_STILL_MS);
  }

  private later(ms: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; this.render(); }, ms);
  }

  private report(sink: Sink): void {
    const kept = sink.kind === "clip" ? this.clip : sink.kind === "wait" ? this.waiting : null;
    this.hooks.report(sink.kind === "live" ? "" : this.unplaced, this.latest?.text ?? "", kept);
  }
}
