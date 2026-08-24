import { describe, it, expect } from "vitest";
import { runSave } from "./useEditMode";

describe("runSave", () => {
  it("returns null when onSave resolves", async () => {
    expect(await runSave({ x: 1 }, async () => {})).toBe(null);
  });

  it("returns the error message when onSave rejects with an Error", async () => {
    expect(
      await runSave({ x: 1 }, async () => { throw new Error("network failed"); }),
    ).toBe("network failed");
  });

  it("stringifies non-Error throws", async () => {
    expect(
      await runSave({ x: 1 }, async () => { throw "raw string throw"; }),
    ).toBe("raw string throw");
  });

  it("does not propagate the rejection — the returned promise always resolves", async () => {
    await expect(
      runSave({ x: 1 }, async () => { throw new Error("boom"); }),
    ).resolves.toBe("boom");
  });
});
