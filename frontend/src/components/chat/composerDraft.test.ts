import { describe, expect, it } from "vitest";
import { draftKeyFor, settingsToApply, type ComposerSettings } from "./composerDraft";

const shown: ComposerSettings = {
  model: "m-default", effort: "", permission_mode: "", mode: "direct",
};
const valid = { models: ["m-default", "m-big"], efforts: ["low", "high"], permissionModes: ["default", "acceptEdits"], modes: ["direct", "auto"] };

describe("draftKeyFor", () => {
  it("keys a blank chat by scope, whatever route opened it", () => {
    expect(draftKeyFor("development", null)).toBe("development::new");
  });
  it("keys a thread by its scope and id", () => {
    expect(draftKeyFor("development", "abc")).toBe("development::abc");
  });
});

describe("settingsToApply", () => {
  it("applies nothing when nothing was chosen", () => {
    expect(settingsToApply({}, shown, valid)).toEqual({});
  });
  it("applies the picks that differ from what is shown", () => {
    expect(settingsToApply(
      { model: "m-big", effort: "high", permission_mode: "acceptEdits", mode: "auto" }, shown, valid,
    )).toEqual({ model: "m-big", effort: "high", permission_mode: "acceptEdits", mode: "auto" });
  });
  it("skips a pick that already matches", () => {
    expect(settingsToApply({ model: "m-default", mode: "direct" }, shown, valid)).toEqual({});
  });
  it("refuses a model, effort, permission mode or chat mode this scope does not offer", () => {
    expect(settingsToApply(
      { model: "gone", effort: "xhigh", permission_mode: "plan", mode: "sideways" }, shown, valid,
    )).toEqual({});
  });
});
