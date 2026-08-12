// Where the words you are still saying live: in the composer, as a draft.
//
// THE RULE THIS ENFORCES: text that has been shown to the operator but not yet
// sent must exist somewhere durable. It used to exist only as React state
// derived from a socket snapshot, rendered as a chat bubble that looked exactly
// like a saved message and was not one, and `VoiceClient.stop()` set that
// snapshot back to empty. Turning voice off therefore deleted the only copy of
// what he had dictated. He lost a long message that way.
//
// The composer draft is already the right home for text you have not sent: it
// is mirrored to the server (`PUT /chat/drafts/{key}`), it survives a reload and
// a different device, and it is where every other unsent word in this app lives.
// Putting dictation there makes the loss impossible by construction rather than
// by remembering to run a cleanup handler.
//
// Voice owns a PREFIX of the composer and nothing else, so the two writers never
// destroy each other:
//
//   [ dictated so far ][ whatever the operator typed ]
//
// A revision swaps the prefix. A commit removes it, leaving the typed remainder
// untouched. If the operator edits inside the dictated prefix, voice has lost
// track of what it owns and RELINQUISHES rather than guessing: the words on
// screen are his now.

/** Replace the slice voice owns, keeping anything typed around it.
 *
 *  `owned` is what voice last wrote; `next` is what it wants to say now.
 *  Returns the new composer contents. */
export function spliceDictation(current: string, owned: string, next: string): string {
  if (owned === next) return current;

  // Nothing dictated yet: put the new words in front of whatever is typed.
  if (!owned) return next && current ? `${next} ${current}` : next || current;

  if (current.startsWith(owned)) {
    const rest = current.slice(owned.length);
    if (!next) {
      // Committed. The dictation left to become a message; drop the separator
      // it was carrying so the typed remainder does not start with a space.
      return rest.replace(/^\s+/, "");
    }
    return next + rest;
  }

  // The operator edited the dictated text. Voice no longer knows which part is
  // its own, so it stops writing rather than overwriting his edit.
  return current;
}

/** Has the operator taken over the dictated text? Once true, voice stops
 *  mirroring until the next utterance begins. */
export function dictationLost(current: string, owned: string): boolean {
  return !!owned && !current.startsWith(owned);
}

/** What the operator actually typed, as opposed to what was dictated into the
 *  box. The engine defers its turn while the composer holds TYPING; mirroring
 *  dictation into the same box must not read as typing, or voice would wait
 *  forever for itself to stop. */
export function typedPortion(current: string, owned: string): string {
  if (owned && current.startsWith(owned)) return current.slice(owned.length).trim();
  return current.trim();
}
