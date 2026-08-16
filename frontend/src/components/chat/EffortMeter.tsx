import { Menu, MenuItem } from "@workshop/ui";
import { effortHint, effortLabel, orderEfforts } from "./effort";
import { nextTurnHint } from "./pickerTiming";

// How hard the model works on this turn, as a ladder you can read without
// opening it: one bar per level the MODEL offers, filled up to the level in use.
//
// The bar count is not fixed. A model with five levels draws five bars and one
// with four draws four, so "filled to here" means the same thing on every model
// instead of implying a rung that model does not have.
//
// It renders NOTHING when the model has no effort control, matching how the
// model picker renders nothing at zero options. A disabled control would be a
// promise that this model could work harder if only you were allowed to ask.

export default function EffortMeter({
  levels, value, onSelect, streaming = false,
}: {
  /** Levels this model honours, as declared by the backend. */
  levels: string[];
  /** The level in use: the operator's choice, or the model's default. */
  value: string;
  onSelect: (level: string) => void;
  /** A turn is in flight, so a pick made now lands on the next message. */
  streaming?: boolean;
}) {
  const ordered = orderEfforts(levels);
  // The model has no effort control. This is NOT nothing: the default chat model
  // (Haiku 4.5) is one of these, so returning null here hid the control on
  // exactly the chat the operator opens first, and the feature read as missing.
  //
  // So it stays in the row, empty and muted, saying which model it is waiting
  // on. No menu, because there is nothing to pick — a menu of one disabled row
  // is a promise that picking is possible.
  if (!ordered.length) {
    return (
      <span
        className="chat-effort is-unavailable"
        title="Effort isn't available on this model. Switch models to set it."
        aria-label="Effort: not available on this model"
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} aria-hidden className="chat-effort__bar" />
        ))}
      </span>
    );
  }

  const at = ordered.indexOf(value);
  // An unrecognised value fills nothing rather than guessing a position.
  const filledTo = at < 0 ? -1 : at;
  const label = effortLabel(value);

  const step = (delta: number) => {
    const next = ordered[Math.min(ordered.length - 1, Math.max(0, filledTo + delta))];
    if (next && next !== value) onSelect(next);
  };

  return (
    <Menu
      label="Effort"
      side="top"
      trigger={
        <button
          className="chat-effort"
          // Never blurs the textarea: the action row cancels pointerdown for
          // every control in it (see keepFocus).
          title={`Effort: ${label}. ${effortHint(value)}${nextTurnHint(streaming)}`}
          aria-label={`Effort: ${label}`}
          onKeyDown={(e) => {
            // Adjust without opening. The menu is for reading what the levels
            // mean; once you know, this is a slider.
            if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); step(1); }
            if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); step(-1); }
          }}
        >
          {ordered.map((lvl, i) => (
            <span
              key={lvl}
              aria-hidden
              className={`chat-effort__bar${i <= filledTo ? " is-on" : ""}`}
            />
          ))}
        </button>
      }
    >
      {/* Strongest first. The menu opens UPWARD from a trigger at the bottom of
          the screen, so the list reads top-to-bottom as max down to low, which
          puts the weakest level nearest the trigger and the ladder the same way
          up as the bars it opens from. Operator ask, 2026-08-14. The ORDER OF
          THE LEVELS themselves is unchanged: orderEfforts stays ascending
          because nearestEffort and the bar fill both depend on it. */}
      {[...ordered].reverse().map((lvl) => (
        <MenuItem key={lvl} checked={lvl === value} onSelect={() => onSelect(lvl)} title={effortHint(lvl)}>
          {effortLabel(lvl)}
        </MenuItem>
      ))}
    </Menu>
  );
}
