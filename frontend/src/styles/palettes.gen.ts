/* =============================================================================
   Workshop Design System — PALETTE LIST (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from frontend/src/styles/palette.mjs.
   The colors themselves are in palettes.css; this is just the menu.
   ========================================================================== */

export type PaletteId = "violet" | "cobalt" | "teal" | "plum" | "sandstone";

export interface PaletteMeta {
  id: PaletteId;
  /** Title-case name shown in Settings. */
  label: string;
  /** One line describing the scheme's character. */
  blurb: string;
}

export const PALETTE_META: readonly PaletteMeta[] = [
  { id: "violet", label: "Violet", blurb: "The Workshop default. Refined violet on inky violet-navy." },
  { id: "cobalt", label: "Cobalt", blurb: "A calm, conventional blue on cool slate surfaces." },
  { id: "teal", label: "Teal", blurb: "Blue-green accent over marine neutrals. The quietest scheme." },
  { id: "plum", label: "Plum", blurb: "Warm magenta accent on plum-tinted neutrals." },
  { id: "sandstone", label: "Sandstone", blurb: "Warm paper and charcoal with a cool indigo accent." },
] as const;

export const DEFAULT_PALETTE: PaletteId = "violet";

export const PALETTE_IDS: readonly PaletteId[] = PALETTE_META.map((p) => p.id);

export function isPaletteId(v: unknown): v is PaletteId {
  return typeof v === "string" && (PALETTE_IDS as readonly string[]).includes(v);
}
