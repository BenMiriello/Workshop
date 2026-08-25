// Priority is a BAND, not a number on a scale.
//
// The store holds one of `urgent | high | medium | low | none`
// (backend db/models.PRIORITIES). The integer the API still carries is the
// FLOOR of each band, so 78 and 40 are not different priorities, they are the
// same one written two ways. Five agents filed carefully graded priorities in a
// single day and every distinction collapsed, because every surface showed the
// integer and nothing said it was a band.
//
// So the UI speaks band names and converts at the edge. The integer stays on
// the wire because the API and 18 callers still use it; changing that is a
// separate migration, and doing it here would mean the same fact in two shapes.

export const PRIORITY_BANDS = ["urgent", "high", "medium", "low", "none"] as const;
export type PriorityBand = (typeof PRIORITY_BANDS)[number];

/** The stored integer for each band: its floor, matching the backend table. */
export const BAND_FLOOR: Record<PriorityBand, number> = {
  urgent: 100, high: 80, medium: 40, low: 1, none: 0,
};

/** The band an integer belongs to. Anything unparseable is `none`. */
export function bandOf(value: number | string | null | undefined): PriorityBand {
  const n = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(n)) return "none";
  for (const band of PRIORITY_BANDS) {
    if (n >= BAND_FLOOR[band]) return band;
  }
  return "none";
}

/** The stored integer for a band name, or 0 for anything unrecognised. */
export function floorOf(band: string): number {
  return BAND_FLOOR[band as PriorityBand] ?? 0;
}

/** What to show in a list or a row. Empty string for `none`, which reads as
 *  absent rather than as a priority called "none". */
export function priorityLabel(value: number | string | null | undefined): string {
  const band = bandOf(value);
  return band === "none" ? "" : band;
}
