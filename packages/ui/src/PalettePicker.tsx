import { Check } from "lucide-react";
import { cx } from "./cx";
import { PALETTE_META, type PaletteId } from "./styles/palettes.gen";

// Picks one of the five color schemes. A view preference: commits on change,
// no Save step (DS rule 7).
//
// Each tile is a LIVE preview, not a painting of one: the miniature carries
// `data-palette` + `data-theme`, which is exactly what palettes.css keys on, so
// it resolves that scheme's real --bg / --card / --accent / status tokens. Add a
// palette to palette.mjs, regenerate, and it appears here correctly with no
// change to this file. `mode` must be a concrete light|dark — pass
// useResolvedMode(theme) so "system" previews what the OS is actually doing.
export function PalettePicker({
  value,
  onChange,
  mode,
  className,
  ariaLabel = "Color scheme",
}: {
  value: PaletteId;
  onChange: (next: PaletteId) => void;
  mode: "light" | "dark";
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={cx("ws-palettes", className)} role="radiogroup" aria-label={ariaLabel}>
      {PALETTE_META.map((p) => {
        const selected = p.id === value;
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={selected}
            title={p.blurb}
            className={cx("ws-palette", selected && "is-selected")}
            onClick={() => onChange(p.id)}
          >
            {/* --_swatch is the scheme's identity color: a moderate, always-
                visible tone at its neutral hue (see identitySwatch() in
                palette.mjs). The live preview below is correct but, at chip
                size, a warm scheme (Sandstone) and a cool one (Cobalt) can
                look the same — near-black/near-white surfaces don't show hue,
                and the one bright element in them (the accent bar) is
                deliberately a cool brand hue even in a warm scheme. The strip
                carries the identity the preview can't. */}
            <span className="ws-palette__preview" data-palette={p.id} data-theme={mode} aria-hidden style={{ ["--_swatch" as string]: p.swatch }}>
              <span className="ws-palette__strip" />
              <span className="ws-palette__card">
                <span className="ws-palette__bar" />
                <span className="ws-palette__dots">
                  <span className="ws-palette__dot ws-palette__dot--ok" />
                  <span className="ws-palette__dot ws-palette__dot--warn" />
                  <span className="ws-palette__dot ws-palette__dot--danger" />
                  <span className="ws-palette__dot" />
                </span>
              </span>
            </span>
            <span className="ws-palette__label">
              {p.label}
              {selected && <Check strokeWidth={2} aria-hidden />}
            </span>
          </button>
        );
      })}
    </div>
  );
}
