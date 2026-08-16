// ONE CONTRACT FOR THE THREE COMPOSER PICKERS: model, effort, permission mode.
//
// All three commit on change (they are per-thread preferences, cheap and
// reversible, so they follow the "commit on change" side of the Design System's
// editability model, not the Save side). And all three apply starting the
// operator's NEXT message: a turn's argv is fixed when its process spawns, so
// nothing picked mid-turn can reach the turn already streaming.
//
// That second half used to be invisible. The controls stayed live-looking while
// a turn ran, with nothing saying the choice was staged, so a mid-turn pick read
// as instant and silently was not. This is the whole of the cue: a sentence on
// the tooltip the control already had, in one place so the three cannot drift
// apart. No disabled states (the pick is still worth making mid-turn, it just
// lands on the next message) and no new chrome in a row of quiet icon buttons.

/** Appended to a picker's tooltip while a turn is in flight. Empty when idle,
 *  because an idle pick applies to the very next thing the operator sends and
 *  needs no explanation. */
export function nextTurnHint(streaming: boolean): string {
  return streaming ? " Applies to your next message." : "";
}
