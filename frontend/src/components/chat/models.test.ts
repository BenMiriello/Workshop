import { describe, expect, it } from "vitest";
import { orderModels } from "./models";

describe("orderModels", () => {
  it("groups by family: Haiku, then Sonnet, then Opus, then Fable", () => {
    const shuffled = [
      "claude-opus-5",
      "claude-sonnet-4-6",
      "claude-fable-5",
      "claude-haiku-4-5-20251001",
      "claude-opus-5-5",
      "claude-fable-5-1",
      "claude-sonnet-5",
    ];
    expect(orderModels(shuffled)).toEqual([
      "claude-haiku-4-5-20251001",
      "claude-sonnet-5",
      "claude-sonnet-4-6",
      "claude-opus-5-5",
      "claude-opus-5",
      "claude-fable-5-1",
      "claude-fable-5",
    ]);
  });

  it("sorts newest first within a family", () => {
    expect(orderModels(["claude-opus-5", "claude-opus-5-5"])).toEqual([
      "claude-opus-5-5",
      "claude-opus-5",
    ]);
  });

  it("puts an unlisted future release of a known family into that family's block", () => {
    // A new Haiku the picker's never seen still belongs at the front, not at
    // the end with every other unrecognized id.
    const ids = ["claude-opus-5", "claude-haiku-6-0", "claude-haiku-4-5-20251001"];
    expect(orderModels(ids)).toEqual([
      "claude-haiku-4-5-20251001",
      "claude-haiku-6-0",
      "claude-opus-5",
    ]);
  });

  it("keeps a wholly unknown family after every named one", () => {
    const ids = ["claude-opus-5", "some-local-model"];
    expect(orderModels(ids)).toEqual(["claude-opus-5", "some-local-model"]);
  });
});
