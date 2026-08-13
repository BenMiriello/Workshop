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
  /** Should the change that produced this state be animated. */
  animate: boolean;
  /** When `open` last changed, epoch ms. */
  since: number;
};

/** NOTHING ANIMATES AWAY A STATE THAT WAS NEVER ON SCREEN.
 *
 *  A re-attach replays the whole turn from offset 0 (the EventSource carries no
 *  Last-Event-ID), so opening a chat on a long turn walks every group open and
 *  shut again inside a few hundred milliseconds. Animating those turned opening
 *  a chat into dozens of animations at once, which the operator reported on
 *  2026-08-12 as "it replays a lot of animations real quick at once".
 *
 *  The first fix for that was to animate the operator's own toggles and nothing
 *  else. That was too broad: an automatic collapse is worth animating precisely
 *  because it explains what just happened, and the operator asked for it back.
 *
 *  So the test is not WHO caused it, it is WHETHER THE READER COULD SEE IT. A
 *  state that lasted less than one animation cannot be animated away, because
 *  there was nothing there to animate away from. A replay's open-then-shut is
 *  milliseconds apart and stays instant; a group that was genuinely open while
 *  work ran animates shut. Same rule, no replay detection, nothing to keep in
 *  sync with the transport. */
export const MIN_VISIBLE_MS = 320;

/** The state a disclosure starts in.
 *
 *  `defaultOpen` is for a group inside a settled turn the reader has opened:
 *  it starts open, but it is not the operator's doing, so it stays automatic.
 *
 *  Never animated: there is no previous state to move from, and animating on
 *  mount is what makes a list of settled groups shimmer as it scrolls in. */
export function openInitial(live: boolean, defaultOpen = false, now = 0): Disclosure {
  return { open: live || defaultOpen, byUser: false, live, animate: false, since: now };
}

/** Work started or finished. Opens on start, closes on finish, and does
 *  neither if the operator has taken control.
 *
 *  Returns the same object when nothing changed, so a caller passing this to a
 *  React state setter re-renders only on a real transition. */
export function onLive(d: Disclosure, live: boolean, now = 0): Disclosure {
  if (live === d.live) return d;
  if (d.byUser) return { ...d, live };
  if (live === d.open) return { ...d, live };
  return { open: live, byUser: false, live, animate: now - d.since >= MIN_VISIBLE_MS, since: now };
}

/** The operator clicked. Flips it, and marks it theirs from now on.
 *
 *  Always animated. A click is the one change that is definitely being watched,
 *  and the motion is what tells the reader their click did something. */
export function onToggle(d: Disclosure, now = 0): Disclosure {
  return { ...d, open: !d.open, byUser: true, animate: true, since: now };
}
