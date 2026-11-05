import { useEffect, useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";
import { cx } from "./cx";

// Number stepper — an editable field with stacked up/down chevrons, replacing
// the browser's native number spinners (which are ugly + inconsistent across
// engines). A view-preference-style control: commits on change. Clamps to
// [min, max]. Styling lives in components.css (.ws-stepper).
//
// The text box keeps its own draft string rather than mirroring `value`
// directly: mirroring it meant every keystroke re-rendered the field back to
// `String(value)` before an in-progress "-", "0." or cleared box ever reached
// a parseable number, so you could never type your way past +-1 by hand.
// The draft only yields to the prop once it stops matching what's typed
// (blur, or the value changing from outside), never mid-edit.
export function Stepper({
  value,
  onChange,
  min = 0,
  max = 999,
  step = 1,
  suffix,
  className,
  ariaLabel,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  className?: string;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const decimals = (String(step).split(".")[1] ?? "").length;
  const round = (v: number) => (decimals ? Number(v.toFixed(decimals)) : v);
  const set = (v: number) => onChange(round(Math.max(min, Math.min(max, v))));

  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    if (parseFloat(draft) !== value) setDraft(String(value));
  }, [value]);

  return (
    <div className={cx("ws-stepper", className)}>
      <input
        className="ws-stepper__input"
        type="text"
        inputMode={decimals ? "decimal" : "numeric"}
        value={draft}
        disabled={disabled}
        aria-label={ariaLabel ?? "value"}
        onChange={(e) => {
          const raw = e.target.value.replace(/[^0-9.-]/g, "");
          setDraft(raw);
          const n = parseFloat(raw);
          if (!Number.isNaN(n)) set(n);
        }}
        onBlur={() => setDraft(String(value))}
      />
      {suffix && <span className="ws-stepper__suffix">{suffix}</span>}
      <div className="ws-stepper__btns">
        <button type="button" tabIndex={-1} aria-label="increment" disabled={disabled || value >= max} onClick={() => set(value + step)}>
          <ChevronUp aria-hidden />
        </button>
        <button type="button" tabIndex={-1} aria-label="decrement" disabled={disabled || value <= min} onClick={() => set(value - step)}>
          <ChevronDown aria-hidden />
        </button>
      </div>
    </div>
  );
}
