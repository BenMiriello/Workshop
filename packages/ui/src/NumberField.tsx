import { useEffect, useState } from "react";
import { cx } from "./cx";

// A numeric text field that can actually be typed into.
//
// Binding a number straight to a text input's `value` and parsing on every
// keystroke (`onChange={(e) => onChange(Number(e.target.value))}`) re-renders
// the field back to the last committed number before an in-progress "-",
// "0." or a fully-cleared box ever reaches a parseable value — the box never
// contains what you just typed. This keeps its own draft string instead, and
// only yields it to `value` on blur or when `value` changes from outside.
// See `Stepper`, which needs the identical fix for its own input.
export function NumberField({
  value,
  onChange,
  min,
  max,
  step,
  className,
  id,
  placeholder,
  disabled,
  onBlur,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  className?: string;
  id?: string;
  placeholder?: string;
  disabled?: boolean;
  onBlur?: () => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    if (parseFloat(draft) !== value) setDraft(String(value));
  }, [value]);

  const clamp = (n: number) => {
    let v = n;
    if (min != null) v = Math.max(min, v);
    if (max != null) v = Math.min(max, v);
    return v;
  };

  return (
    <input
      id={id}
      className={cx("ws-input", className)}
      type="text"
      inputMode={step != null && step % 1 !== 0 ? "decimal" : "numeric"}
      value={draft}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => {
        const raw = e.target.value.replace(/[^0-9.-]/g, "");
        setDraft(raw);
        const n = parseFloat(raw);
        if (!Number.isNaN(n)) onChange(clamp(n));
      }}
      onBlur={() => { setDraft(String(value)); onBlur?.(); }}
    />
  );
}
