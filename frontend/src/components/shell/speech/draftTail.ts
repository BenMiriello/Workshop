// The words still being said, into the draft of a chat that is not on
// screen: written at the draft's end as they are heard and rewritten there as
// they are revised, the way a field on screen shows them. Without this a chat
// you had left received words only once they settled, so a sentence still
// being said was missing from it for as long as you kept talking.

import { replaceDraftTail } from "../../../lib/draftWire";
import type { ChatTarget } from "../../../lib/speech/chatTargets";
import { joinSaid } from "../../../lib/speech/insertText";
import type { LiveFinal } from "../../../lib/speech/liveWire";

// How often the words still being said are rewritten into the draft.
const EVERY_MS = 700;

type Tail = { key: string; text: string; to: number; name: string };

export type TailHooks = {
  /** Settled words went to `place`. */
  placed: (text: string, place: string) => void;
  /** The words through `to` stay as they are; the next stretch starts there. */
  letGo: (to: number) => void;
};

export class DraftTail {
  private tail: Tail | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private hooks: TailHooks) {}

  /** Whether this chat's draft holds the words still being said. */
  holds(chat: ChatTarget): boolean {
    return this.tail?.key === chat.draftKey;
  }

  /** The open stretch is now `latest`: rewrite it into `chat`'s draft soon. */
  show(chat: ChatTarget, name: string, latest: () => { text: string; to: number } | null): void {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      const now = latest();
      if (!now) return;
      const was = this.holds(chat) ? this.tail!.text : "";
      if (was === now.text) return;
      void replaceDraftTail(chat.draftKey, was, now.text).catch(() => {});
      this.tail = { key: chat.draftKey, text: now.text, to: now.to, name };
    }, this.tail ? EVERY_MS : 0);
  }

  /** Part of the stretch settled: in the draft it becomes the settled text
   *  followed by the rest, in place. */
  settle(chat: ChatTarget, name: string, m: LiveFinal): void {
    this.cancel();
    const was = this.holds(chat) ? this.tail!.text : "";
    void replaceDraftTail(chat.draftKey, was, joinSaid(m.text, m.rest)).catch(() => {});
    this.tail = m.rest ? { key: chat.draftKey, text: m.rest, to: m.restTo, name } : null;
    if (m.text) this.hooks.placed(m.text, name);
  }

  /** Words now go somewhere else: what the draft holds stays there. */
  freeze(): void {
    this.cancel();
    const tail = this.tail;
    this.tail = null;
    if (!tail?.text) return;
    this.hooks.placed(tail.text, tail.name);
    this.hooks.letGo(tail.to);
  }

  cancel(): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
  }

  get active(): boolean {
    return !!this.tail;
  }

  key(): string | null {
    return this.tail?.key ?? null;
  }
}
