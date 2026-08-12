/* Types for palette.mjs. The model is authored in plain JS so `node
   scripts/gen-tokens.mjs` can import it without a build step; this is what lets
   TypeScript (and the vitest suite) consume the same module. */

/** One scheme's seed: three agency hues plus the per-scheme trims. */
export interface SchemeSeed {
  id: string;
  label: string;
  blurb: string;
  /** agency hues, OKLCh degrees: you / system / autonomous */
  base: number;
  sys: number;
  auto: number;
  /** chroma multipliers per tier */
  yc?: number;
  sc?: number;
  ac?: number;
  /** lightness offsets per tier (dark mode only) */
  yl?: number;
  sl?: number;
  al?: number;
  /** surface-chroma multiplier */
  bgC?: number;
  /** member of the one-hue set */
  quiet?: boolean;
}

/** Every semantic color of one scheme: camelCase token -> css color string. */
export type Scheme = Record<string, string>;

/** The three agency tones of a scheme, for the Settings picker only. */
export interface IdentitySwatch {
  you: string;
  sys: string;
  auto: string;
}

export const SCHEMES: SchemeSeed[];
/** Back-compat alias for SCHEMES. */
export const PALETTES: SchemeSeed[];
export const DEFAULT_PALETTE: string;
export const MODES: readonly ["light", "dark"];
export const CATEGORICAL: Record<string, string>;
export const LEGACY_PALETTES: Record<string, string>;

export function paletteById(id: string): SchemeSeed;
export function buildScheme(schemeId: string, mode: "light" | "dark"): Scheme;
export function allSchemes(): Record<string, Scheme>;
export function identitySwatch(schemeId: string): IdentitySwatch;

export function oklch(L: number, C: number, H: number): string;
export function hexToRgb(hex: string): [number, number, number];
export function luminance(hex: string): number;
export function contrast(a: string, b: string): number;
export function alpha(hex: string, a: number): string;
export function solveTone(
  startL: number,
  C: number,
  H: number,
  against: string[],
  target: number,
  dir: number,
): number;
