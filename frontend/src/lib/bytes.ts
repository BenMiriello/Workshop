// Byte sizes, in one place. Three independent copies of a `fmtBytes` existed
// on 2026-08-17 (`pages/Memory.tsx`, `pages/apps/birds/recordings.tsx`,
// `components/AutonomyControls.tsx`) and, unlike the six `fmtTs` copies that
// produced `lib/time.ts`, they were not the same function wearing one name:
//
//   Memory          B / KB / MB, and nothing above it, so a 2 GiB file read
//                   "2048.0 MB"
//   birds           B / KB / MB / GB, the same ladder with the tier Memory
//                   was missing
//   AutonomyControls  B / MiB / GiB in IEC words, nullable, "-" when unknown
//
// The first two are one function and are now `formatBytes`. The third is a
// different one and stays different: it reports VRAM, which the driver, the
// broker and the model catalog all quote in MiB, and mislabelling a mebibyte
// as a megabyte on the page whose whole job is fitting a model into a card is
// the sort of quiet wrongness this repo keeps finding. It is here rather than
// in the component so it can be executed by a test, and named so nobody
// reaches for it by accident.
//
// This module is PURE: no React, no fetch, no imports. Everything in it is a
// number in and a string out.

/** Human byte size: B, KB, MB, GB. Binary divisors with the short words, the
 * convention every file listing in this app already used.
 *
 * Precision falls as the unit rises, because the digits stop being worth
 * their width: whole bytes, whole KB, tenths of an MB, hundredths of a GB. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** The placeholder a VRAM figure shows when the source could not report one.
 * An en dash, not "0" and not "unknown": the row is a dense numeric column
 * and a missing number should read as a gap, not as a measurement. */
export const VRAM_UNKNOWN = "–";

/** VRAM in bytes, in IEC words (MiB / GiB), for the model catalog and the
 * broker readout. `null` when the driver did not report a size. */
export function formatVramBytes(bytes: number | null): string {
  if (bytes == null || !Number.isFinite(bytes)) return VRAM_UNKNOWN;
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  return `${bytes} B`;
}

/** VRAM already counted in MiB, which is the unit the broker's budgets, the
 * leases and nvidia's own totals arrive in. Promoted to GiB past 1024 so a
 * 24 GiB card does not read as a five-digit number. */
export function formatMiB(mib: number | undefined): string {
  if (mib == null || !Number.isFinite(mib)) return VRAM_UNKNOWN;
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GiB` : `${mib} MiB`;
}
