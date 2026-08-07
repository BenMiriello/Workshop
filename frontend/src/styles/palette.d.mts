/* Types for palette.mjs. The model is authored in plain JS so `node
   scripts/gen-tokens.mjs` can import it without a build step; this is what lets
   TypeScript (and the vitest suite) consume the same module. */

export interface PaletteSeed {
  id: string;
  label: string;
  blurb: string;
  brandHue: number;
  neutralHue: number;
  brandChroma: number;
  neutralChroma: number;
}

/** Every semantic color of one scheme: camelCase token -> css color string. */
export type Scheme = Record<string, string>;

export const PALETTES: PaletteSeed[];
export const DEFAULT_PALETTE: string;
export const MODES: readonly ["light", "dark"];
export const CATEGORICAL: Record<string, string>;

export function paletteById(id: string): PaletteSeed;
export function buildScheme(paletteId: string, mode: "light" | "dark"): Scheme;
export function allSchemes(): Record<string, Scheme>;

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
