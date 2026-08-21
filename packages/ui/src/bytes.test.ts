// The pure byte formatters. These assert the EXACT strings the three copies
// this module replaced produced, because the point of consolidating them was
// that nothing on screen changed, with one deliberate exception noted below.
import { describe, expect, it } from "vitest";
import { VRAM_UNKNOWN, formatBytes, formatMiB, formatVramBytes } from "./bytes";

const KB = 1024;
const MB = 1024 * 1024;
const GB = 1024 * 1024 * 1024;

describe("formatBytes", () => {
  it("steps B, KB, MB, GB", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(KB)).toBe("1 KB");
    expect(formatBytes(1536)).toBe("2 KB");
    expect(formatBytes(MB)).toBe("1.0 MB");
    expect(formatBytes(3 * MB + 512 * KB)).toBe("3.5 MB");
    expect(formatBytes(GB)).toBe("1.00 GB");
    expect(formatBytes(2.5 * GB)).toBe("2.50 GB");
  });

  it("has a GB tier, which Memory's copy did not", () => {
    // The one output change from the merge: `pages/Memory.tsx` stopped at MB,
    // so a 2 GiB stored file read "2048.0 MB" in the files table and "2.00 GB"
    // everywhere else. Under a gigabyte the two copies agreed byte for byte.
    expect(formatBytes(2 * GB)).toBe("2.00 GB");
  });

  it("whole bytes below a KB, no fractional byte", () => {
    expect(formatBytes(1)).toBe("1 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("is empty rather than NaN for a non-finite size", () => {
    expect(formatBytes(Number.NaN)).toBe("");
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("");
  });
});

describe("formatVramBytes", () => {
  it("uses IEC words, because VRAM is quoted in mebibytes everywhere it comes from", () => {
    expect(formatVramBytes(512)).toBe("512 B");
    expect(formatVramBytes(4 * MB)).toBe("4 MiB");
    expect(formatVramBytes(6 * GB)).toBe("6.0 GiB");
  });

  it("says nothing measurable when the driver reported nothing", () => {
    expect(formatVramBytes(null)).toBe(VRAM_UNKNOWN);
    expect(formatVramBytes(Number.NaN)).toBe(VRAM_UNKNOWN);
  });

  it("is NOT formatBytes: the same number reads differently on purpose", () => {
    expect(formatVramBytes(6 * GB)).not.toBe(formatBytes(6 * GB));
  });
});

describe("formatMiB", () => {
  it("promotes to GiB past 1024 MiB", () => {
    expect(formatMiB(512)).toBe("512 MiB");
    expect(formatMiB(1024)).toBe("1.0 GiB");
    expect(formatMiB(24564)).toBe("24.0 GiB");
  });

  it("says nothing measurable when the figure is missing", () => {
    expect(formatMiB(undefined)).toBe(VRAM_UNKNOWN);
  });
});
