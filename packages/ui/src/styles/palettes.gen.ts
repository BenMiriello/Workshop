/* =============================================================================
   Workshop Design System — PALETTE LIST (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from packages/ui/src/styles/palette.mjs.
   The colors themselves are in palettes.css; this is just the menu.
   ========================================================================== */

export type PaletteId = "violet" | "cobalt" | "teal" | "plum" | "sandstone";

export interface PaletteMeta {
  id: PaletteId;
  /** Title-case name shown in Settings. */
  label: string;
  /** One line describing the scheme's character. */
  blurb: string;
  /** Mode-independent identity color for the picker swatch. See palette.mjs identitySwatch(). */
  swatch: string;
}

export const PALETTE_META: readonly PaletteMeta[] = [
  { id: "violet", label: "Violet", blurb: "The Workshop default. Refined violet on inky violet-navy.", swatch: "#8f88cc" },
  { id: "cobalt", label: "Cobalt", blurb: "A calm, conventional blue on cool slate surfaces.", swatch: "#7292cd" },
  { id: "teal", label: "Teal", blurb: "Blue-green accent over marine neutrals. The quietest scheme.", swatch: "#45a1b3" },
  { id: "plum", label: "Plum", blurb: "Warm magenta accent on plum-tinted neutrals.", swatch: "#ad81ab" },
  { id: "sandstone", label: "Sandstone", blurb: "Warm paper and charcoal with a cool indigo accent.", swatch: "#b48952" },
] as const;

export const DEFAULT_PALETTE: PaletteId = "violet";

export const PALETTE_IDS: readonly PaletteId[] = PALETTE_META.map((p) => p.id);

export function isPaletteId(v: unknown): v is PaletteId {
  return typeof v === "string" && (PALETTE_IDS as readonly string[]).includes(v);
}
