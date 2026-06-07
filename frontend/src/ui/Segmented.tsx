import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "./cx";

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

// Segmented control with the sliding white thumb (reimplements kitchen.js
// initSegmented in React). The thumb is positioned over the active button via
// measured offsetLeft/offsetWidth; CSS falls back to a static indicator if the
// measurement hasn't run. Commits on change — it's a view-preference control.
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
  ariaLabel,
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const measure = () => {
      const active = root.querySelector<HTMLButtonElement>('button[aria-pressed="true"]');
      if (active) setThumb({ left: active.offsetLeft, width: active.offsetWidth });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    // Re-measure once web fonts settle (label widths shift).
    if (document.fonts?.ready) void document.fonts.ready.then(measure);
    return () => ro.disconnect();
  }, [value, options.length]);

  return (
    <div ref={ref} className={cx("ws-segmented", thumb && "has-thumb", className)} role="group" aria-label={ariaLabel}>
      {thumb && (
        <span
          className="ws-segmented__thumb"
          style={{ width: thumb.width, transform: `translateX(${thumb.left}px)`, opacity: 1 }}
        />
      )}
      {options.map((opt) => (
        <button key={opt.value} type="button" aria-pressed={opt.value === value} onClick={() => onChange(opt.value)}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}
