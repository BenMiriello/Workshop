import { describe, expect, it } from "vitest";

import { PERMISSION_MODE_IDS, permissionModeDef } from "./permissionModes";

// The hint is the only thing that tells the operator where a mode stops to ask,
// and it was wrong for the mode every chat runs at by default. `auto` said "acts
// without asking" while the backend launches every mode but `bypassPermissions`
// with an approval channel (`model_caps.NEEDS_APPROVAL_CHANNEL`), so an auto
// turn does stop when the safety check gates a call. Reported 2026-09-17: "it
// should not be asking me for permissions when it is in auto mode."
describe("permission mode hints", () => {
  it("does not promise the absence of a prompt except in the one mode that has none", () => {
    for (const id of PERMISSION_MODE_IDS) {
      const { hint } = permissionModeDef(id);
      if (id === "bypassPermissions") continue;
      expect(hint.toLowerCase()).not.toContain("acts without asking");
    }
  });

  it("says what auto stops for, not only what it runs", () => {
    const { hint } = permissionModeDef("auto");
    expect(hint.toLowerCase()).toContain("stops");
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
