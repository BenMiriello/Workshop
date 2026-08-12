// WHO OPENED IT, AND THEREFORE WHO IS ALLOWED TO CLOSE IT.
//
// A group of steps opens itself while its work is in flight (assistant-ui's
// auto-expand during streaming) and closes itself again when that work
// finishes, so a long turn does not leave a trail of opened groups behind it.
// Before this, a group opened on the way past and stayed open forever, so a
// turn watched live looked nothing like the same turn opened afterwards.
//
// The whole difficulty is that "close it when it finishes" must never close
// something the OPERATOR opened. If it could, reading a group from earlier in a
// running turn would be impossible: the next step to finish would shut it
// again, and there would be no way to hold it open. Operator instruction,
// 2026-08-12, who called this out before it was built.
//
// So the open flag carries its PROVENANCE, and the rule is one sentence:
// something automatic may only move a disclosure that the machine itself last
// moved. The moment the operator touches it, it is theirs and nothing automatic
// moves it again.
//
// That cuts both ways on purpose. An operator who CLOSES a group mid-run does
// not get it reopened by the next step, which is the same principle: their
// action is the last word, whichever direction it went.
//
// Pure and total, so the rule is executable in a test rather than only
// observable by watching a turn stream past. There is no DOM harness here and
// none is planned; extracting the rule is how behaviour gets pinned.

export type Disclosure = {
  /** Is it showing right now. */
  open: boolean;
  /** The operator has taken control of this one. Nothing automatic may move it. */
  byUser: boolean;
  /** Whether work was in flight the last time this was updated. Kept so a
   *  change can be distinguished from a re-render at the same state: without
   *  it, "not live" on first mount is indistinguishable from "just finished",
   *  and a settled group opened by the reader would slam shut immediately. */
  live: boolean;
};

/** The state a disclosure starts in.
 *
 *  `defaultOpen` is for a group inside a settled turn the reader has opened:
 *  it starts open, but it is not the operator's doing, so it stays automatic. */
export function openInitial(live: boolean, defaultOpen = false): Disclosure {
  return { open: live || defaultOpen, byUser: false, live };
}

/** Work started or finished. Opens on start, closes on finish, and does
 *  neither if the operator has taken control.
 *
 *  Returns the same object when nothing changed, so a caller passing this to a
 *  React state setter re-renders only on a real transition. */
export function onLive(d: Disclosure, live: boolean): Disclosure {
  if (live === d.live) return d;
  if (d.byUser) return { ...d, live };
  return { open: live, byUser: false, live };
}

/** The operator clicked. Flips it, and marks it theirs from now on. */
export function onToggle(d: Disclosure): Disclosure {
  return { ...d, open: !d.open, byUser: true };
}
