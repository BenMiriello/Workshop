import { ChevronUp, ChevronDown } from "lucide-react";
import { cx } from "./cx";

// Number stepper — an editable field with stacked up/down chevrons, replacing
// the browser's native number spinners (which are ugly + inconsistent across
// engines). A view-preference-style control: commits on change. Clamps to
// [min, max]. Styling lives in components.css (.ws-stepper).
export function Stepper({
  value,
  onChange,
  min = 0,
  max = 999,
  step = 1,
  suffix,
  className,
  ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  className?: string;
  ariaLabel?: string;
}) {
  const set = (v: number) => onChange(Math.max(min, Math.min(max, v)));
  return (
    <div className={cx("ws-stepper", className)}>
      <input
        className="ws-stepper__input"
        type="text"
        inputMode="numeric"
        value={value}
        aria-label={ariaLabel ?? "value"}
        onChange={(e) => {
          const n = parseInt(e.target.value.replace(/[^0-9-]/g, ""), 10);
          if (!Number.isNaN(n)) set(n);
        }}
      />
      {suffix && <span className="ws-stepper__suffix">{suffix}</span>}
      <div className="ws-stepper__btns">
        <button type="button" tabIndex={-1} aria-label="increment" disabled={value >= max} onClick={() => set(value + step)}>
          <ChevronUp aria-hidden />
        </button>
        <button type="button" tabIndex={-1} aria-label="decrement" disabled={value <= min} onClick={() => set(value - step)}>
          <ChevronDown aria-hidden />
        </button>
      </div>
    </div>
  );
}
