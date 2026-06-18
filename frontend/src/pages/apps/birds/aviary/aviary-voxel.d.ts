// Types for the voxel renderer (aviary-voxel.js).
export interface BirdColors {
  back: string; belly?: string; wing?: string; throat?: string;
  cap?: string; face?: string; bill?: string; legs?: string; tailTip?: string;
}
export interface BirdProfile {
  id?: string;
  common: string;
  sci: string;
  template: string;
  group: string;
  pattern: string;
  colors: BirdColors;
}
export const AviaryVoxel: {
  SPECIES: Record<string, BirdProfile>;
  YARD_IDS: string[];
  buildBird(spec: BirdProfile): unknown;
  renderBird(model: unknown, pose: unknown): string;
  [k: string]: unknown;
};
