// The one notice the dictation bar shows at a time (dictationState.ts has its
// shape): shown for five seconds unless said otherwise, replaced by the next.

import type { Notice } from "./dictationState";

export const NOTICE_MS = 5000;

export type NoticeInput = Omit<Notice, "id" | "since" | "until"> & { until?: number | null };

export class Notices {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private id = 0;

  constructor(private current: () => Notice | null, private show: (n: Notice | null) => void) {}

  set(n: NoticeInput | null): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (!n) { this.show(null); return; }
    const since = Date.now();
    const until = n.until === undefined ? since + NOTICE_MS : n.until;
    const shown: Notice = { ...n, id: ++this.id, since, until };
    this.show(shown);
    if (until === null) return;
    this.timer = setTimeout(() => {
      if (this.current()?.id !== shown.id) return;
      this.show(null);
    }, until - Date.now());
  }
}
