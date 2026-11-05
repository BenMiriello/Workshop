// WHICH CONVERSATION A SCOPE OPENS WHEN THE URL NAMES NONE.
//
// The order is documented at the top of useChatThread: the thread this tab is
// already on, then a recorded "new chat", then the last thread picked in this
// scope, then the newest. That selection is kept per scope in this browser.
//
// AN APP'S CHAT FRAME PUTS A RUNNING TURN FIRST. The frame sits inside the app's
// own screen with no list beside it, and the last pick is per browser, so a
// second device kept opening the conversation it last saw. On 2026-10-03 the
// operator's other device opened an older, finished chat, the one whose turn
// they really had stopped, three times while the newest chat's turn ran for an
// hour; "it's been stopped" was true of the chat on screen and false of the one
// spending. A turn in flight is the thing the frame most needs to show, and its
// Stop with it.

/** The fields of a thread row this choice reads. */
export type FallbackRow = { id: string; running?: boolean };

export function pickFallbackThread({
  current, stored, inScope, preferRunning, newChat,
}: {
  /** The thread this tab already has open, if any. */
  current: string | null;
  /** The scope's recorded selection in this browser. */
  stored: string | null;
  /** This scope's threads, newest first. */
  inScope: FallbackRow[];
  /** An app's chat frame: a running turn outranks the recorded selection. */
  preferRunning: boolean;
  /** The value the selection store uses for "a blank chat was asked for". */
  newChat: string;
}): string | null {
  const has = (id: string | null) => !!id && inScope.some((x) => x.id === id);
  if (has(current)) return current;
  if (preferRunning) {
    const running = inScope.find((x) => x.running);
    if (running) return running.id;
  }
  if (stored === newChat) return null;
  if (has(stored)) return stored;
  return inScope.length ? inScope[0].id : null;
}
