import { Check } from "lucide-react";
import { cx } from "./cx";
import { PALETTE_META, type PaletteGroup, type PaletteId, type PaletteMeta } from "./styles/palettes.gen";

// Picks one of the eight color schemes. A view preference: commits on change,
// no Save step (DS rule 7).
//
// Each tile is a LIVE preview, not a painting of one: the miniature carries
// `data-palette` + `data-theme`, which is exactly what palettes.css keys on, so
// it resolves that scheme's real --bg / --card / --you / status tokens. Add a
// scheme to palette.mjs, regenerate, and it appears here in the right group with
// no change to this file. `mode` must be a concrete light|dark — pass
// useResolvedMode(theme) so "system" previews what the OS is actually doing.
//
// THE TWO GROUPS ARE NOT DECORATION. The expressive set spends three hues on
// the agency tiers; the quiet set spends one and separates them by chroma. That
// is a different kind of choice, so they are separate rows with a divider
// rather than one flat row of eight.
const GROUPS: { id: PaletteGroup; label: string }[] = [
  { id: "expressive", label: "Expressive" },
  { id: "quiet", label: "Quiet" },
];

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
    <div className={className} role="radiogroup" aria-label={ariaLabel}>
      {GROUPS.map((g) => {
        const items = PALETTE_META.filter((p) => p.group === g.id);
        if (!items.length) return null;
        return (
          <div key={g.id} className="ws-palette-group">
            <div className="ws-palette-group__label">{g.label}</div>
            <div className="ws-palettes">
              {items.map((p) => (
                <PaletteTile key={p.id} p={p} mode={mode} selected={p.id === value} onChange={onChange} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PaletteTile({
  p, mode, selected, onChange,
}: {
  p: PaletteMeta;
  mode: "light" | "dark";
  selected: boolean;
  onChange: (next: PaletteId) => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      title={p.blurb}
      className={cx("ws-palette", selected && "is-selected")}
      onClick={() => onChange(p.id)}
    >
      {/* The strip is the scheme's IDENTITY: its three agency tones side by
          side, mode-independent and at moderate chroma (identitySwatch() in
          palette.mjs). The live preview below is correct but nearly hue-less at
          chip size, since near-black and near-white don't show hue, so a warm
          scheme and a cool one can look the same in 76px. And what is actually
          being chosen is the RELATIONSHIP between you / system / autonomous, so
          showing one brand color would name the wrong thing. */}
      <span
        className="ws-palette__preview"
        data-palette={p.id}
        data-theme={mode}
        aria-hidden
        style={{
          ["--_sw-you" as string]: p.swatch.you,
          ["--_sw-sys" as string]: p.swatch.sys,
          ["--_sw-auto" as string]: p.swatch.auto,
        }}
      >
        <span className="ws-palette__strip">
          <span /><span /><span />
        </span>
        <span className="ws-palette__card">
          <span className="ws-palette__bar" />
          <span className="ws-palette__dots">
            <span className="ws-palette__dot ws-palette__dot--auto" />
            <span className="ws-palette__dot ws-palette__dot--ok" />
            <span className="ws-palette__dot ws-palette__dot--warn" />
            <span className="ws-palette__dot ws-palette__dot--danger" />
          </span>
        </span>
      </span>
      <span className="ws-palette__label">
        {p.label}
        {selected && <Check strokeWidth={2} aria-hidden />}
      </span>
    </button>
  );
}
