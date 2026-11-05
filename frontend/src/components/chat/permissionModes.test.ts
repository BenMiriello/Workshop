import { describe, expect, it } from "vitest";

import { PERMISSION_MODE_IDS, permissionModeDef } from "./permissionModes";

// The hint is the only thing that tells the operator where a mode stops to ask.
// Every mode but `bypassPermissions` is launched with an approval channel
// (`model_caps.NEEDS_APPROVAL_CHANNEL`), and `auto` still asks before a delete.
describe("permission mode hints", () => {
  it("does not promise the absence of a prompt except in the one mode that has none", () => {
    for (const id of PERMISSION_MODE_IDS) {
      const { hint } = permissionModeDef(id);
      if (id === "bypassPermissions") continue;
      expect(hint.toLowerCase()).not.toContain("acts without asking");
    }
  });

  it("says what auto asks about, not only what it runs", () => {
    const { hint } = permissionModeDef("auto");
    expect(hint.toLowerCase()).toContain("deleting");
  });

  it("never hedges, in either direction", () => {
    for (const id of PERMISSION_MODE_IDS) {
      const lowered = permissionModeDef(id).hint.toLowerCase();
      for (const hedge of ["if available", "may ask", "might ask", "sometimes"]) {
        expect(lowered).not.toContain(hedge);
      }
    }
  });

  it("renders an unknown id under its own name rather than vanishing", () => {
    expect(permissionModeDef("somethingNew").label).toBe("somethingNew");
    expect(permissionModeDef(undefined).label).toBe("Auto");
  });
});
