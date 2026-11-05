// The dictation controller's state as a small external store, so the chip,
// the bar and the shortcuts all read one value (useSyncExternalStore), and the
// one notice the bar shows.

import { IDLE, type DictationState } from "./dictationState";
import { Notices, type NoticeInput } from "./notices";

const ERROR_SHOWN_MS = 5000;

export class DictationStore {
  protected state: DictationState = IDLE;
  private listeners = new Set<() => void>();
  private notices = new Notices(() => this.state.notice, (notice) => this.set({ notice }));
  private errorTimer: ReturnType<typeof setTimeout> | null = null;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  getState = () => this.state;

  protected set(patch: Partial<DictationState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  /** Show `n` in the bar for a few seconds; null clears it. */
  notice(n: NoticeInput | null): void {
    this.notices.set(n);
  }

  get busy(): boolean {
    const p = this.state.phase;
    return p === "starting" || p === "listening" || p === "finishing";
  }

  get listening(): boolean {
    return this.state.phase === "starting" || this.state.phase === "listening";
  }

  /** Show what went wrong: for a few seconds, or until dismissed when the
   *  microphone was refused. */
  protected showError(message: string, blocked: boolean): void {
    this.clearErrorTimer();
    this.set({ ...IDLE, originEl: this.state.originEl, phase: "error", error: message, blocked });
    if (blocked) return;
    this.errorTimer = setTimeout(() => {
      if (this.state.phase === "error") this.set({ phase: "idle", error: null });
    }, ERROR_SHOWN_MS);
  }

  protected clearErrorTimer(): void {
    if (this.errorTimer) { clearTimeout(this.errorTimer); this.errorTimer = null; }
  }

  dismiss(): void {
    if (this.state.phase !== "error") return;
    this.clearErrorTimer();
    this.set({ phase: "idle", error: null, blocked: false });
  }
}
