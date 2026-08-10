import { Menu, MenuItem } from "../../ui";
import { effortHint, effortLabel, orderEfforts } from "./effort";

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
  levels, value, onSelect,
}: {
  /** Levels this model honours, as declared by the backend. */
  levels: string[];
  /** The level in use: the operator's choice, or the model's default. */
  value: string;
  onSelect: (level: string) => void;
}) {
  const ordered = orderEfforts(levels);
  if (!ordered.length) return null;

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
      align="end"
      side="top"
      trigger={
        <button
          className="chat-effort"
          // Same rule as every other button in this row: never blur the textarea.
          onMouseDown={(e) => e.preventDefault()}
          title={`Effort: ${label}. ${effortHint(value)}`}
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
      {ordered.map((lvl) => (
        <MenuItem key={lvl} checked={lvl === value} onSelect={() => onSelect(lvl)} title={effortHint(lvl)}>
          {effortLabel(lvl)}
        </MenuItem>
      ))}
    </Menu>
  );
}
