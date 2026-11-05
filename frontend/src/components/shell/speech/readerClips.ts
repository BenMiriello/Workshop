// The service's audio for one read, fetched a sentence at a time and kept, so
// going back a sentence plays what was already fetched instead of asking again.
// Cancelling ends every fetch still in flight.

import { speechApi } from "../../../lib/speech/api";

export class ServiceClips {
  private fetched = new Map<number, Promise<Blob>>();
  private ready = new Set<number>();
  private abort = new AbortController();

  constructor(private pieces: string[], private voice: string | undefined) {}

  get(i: number): Promise<Blob> {
    let p = this.fetched.get(i);
    if (!p) {
      p = speechApi.speak(this.pieces[i], this.voice, this.abort.signal);
      p.then(() => { this.ready.add(i); }, () => {});
      this.fetched.set(i, p);
    }
    return p;
  }

  /** The clip has arrived, so playing it starts at once. */
  has(i: number): boolean {
    return this.ready.has(i);
  }

  cancel(): void {
    this.abort.abort();
  }
}
