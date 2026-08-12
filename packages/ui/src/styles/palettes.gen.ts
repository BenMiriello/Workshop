/* =============================================================================
   Workshop Design System — SCHEME LIST (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from packages/ui/src/styles/palette.mjs.
   The colors themselves are in palettes.css; this is just the menu.
   ========================================================================== */

export type PaletteId = "lagoon" | "iris" | "cobalt" | "fern" | "clay" | "slate" | "linen" | "graphite";

/** Which row of the Settings picker a scheme belongs to. The quiet set is a
    different KIND of choice (one hue, tiers by chroma), not three more colors,
    so the picker shows the two groups separately. */
export type PaletteGroup = "expressive" | "quiet";

export interface PaletteMeta {
  id: PaletteId;
  /** Title-case name shown in Settings. */
  label: string;
  /** One line describing the scheme's character. */
  blurb: string;
  group: PaletteGroup;
  /** The scheme's three agency tones, mode-independent, for the picker's
      identity strip. See palette.mjs identitySwatch(). */
  swatch: { you: string; sys: string; auto: string };
}

export const PALETTE_META: readonly PaletteMeta[] = [
  { id: "lagoon", label: "Lagoon", blurb: "Cyan base, pink system, orange escalation.", group: "expressive", swatch: {"you":"#00a7a7","sys":"#c96fa4","auto":"#e36b02"} },
  { id: "iris", label: "Iris", blurb: "Orchid base, blue system, indigo escalation.", group: "expressive", swatch: {"you":"#b378c1","sys":"#00a2c5","auto":"#5c8df8"} },
  { id: "cobalt", label: "Cobalt", blurb: "Blue base, teal system, azure escalation.", group: "expressive", swatch: {"you":"#6693d6","sys":"#00aa95","auto":"#00a1cd"} },
  { id: "fern", label: "Fern", blurb: "Green base, violet system, rose escalation.", group: "expressive", swatch: {"you":"#4aa77a","sys":"#9a80d8","auto":"#c26cc7"} },
  { id: "clay", label: "Clay", blurb: "Terracotta base, blue system, electric-cyan escalation.", group: "expressive", swatch: {"you":"#d07757","sys":"#5296df","auto":"#00a7a9"} },
  { id: "slate", label: "Slate", blurb: "Cool blue-gray. One hue, tiers by chroma.", group: "quiet", swatch: {"you":"#6a97c2","sys":"#8295a6","auto":"#00a5b3"} },
  { id: "linen", label: "Linen", blurb: "Warm sand. One hue, tiers by chroma.", group: "quiet", swatch: {"you":"#b68764","sys":"#a28e7f","auto":"#d8732b"} },
  { id: "graphite", label: "Graphite", blurb: "Near-achromatic with a violet cast.", group: "quiet", swatch: {"you":"#7d91c5","sys":"#8a92a7","auto":"#aa7cc6"} },
] as const;

export const DEFAULT_PALETTE: PaletteId = "iris";

export const PALETTE_IDS: readonly PaletteId[] = PALETTE_META.map((p) => p.id);

export function isPaletteId(v: unknown): v is PaletteId {
  return typeof v === "string" && (PALETTE_IDS as readonly string[]).includes(v);
}

/** Scheme ids from before the agency model, mapped to their nearest surviving
    scheme, so a stored preference moves across instead of snapping to the
    default. Unknown ids still fall back to DEFAULT_PALETTE. */
export const LEGACY_PALETTES: Record<string, PaletteId> = {
  violet: "iris",
  plum: "iris",
  teal: "lagoon",
  sandstone: "linen",
};

/** The id to actually apply for a stored value: itself, its replacement, or the
    default. One function so the bootstrap, the hook and the tests agree. */
export function resolvePaletteId(v: unknown): PaletteId {
  if (isPaletteId(v)) return v;
  if (typeof v === "string" && LEGACY_PALETTES[v]) return LEGACY_PALETTES[v];
  return DEFAULT_PALETTE;
}
