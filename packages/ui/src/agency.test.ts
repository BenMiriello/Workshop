import { describe, it, expect } from "vitest";
import { agencyFor } from "./agency";

// WHO DID THIS, from what the platform actually recorded. The mapping is pinned
// because the two failure modes are asymmetric and both are bad: calling a run
// you started `autonomous` raises a false alarm, and calling a cron sweep
// `system` claims you were present for something nobody watched.
describe("agencyFor maps the platform's trigger vocabulary", () => {
  it("`manual` is you, and nothing else is", () => {
    expect(agencyFor("manual")).toBe("you");
  });

  it("a conversation you are in is `system`, however the work reaches it", () => {
    // A Discord-triggered agent is answering YOU. The surface is not the tier.
    expect(agencyFor("chat")).toBe("system");
    expect(agencyFor("discord")).toBe("system");
  });

  it("only self-starting work is autonomous", () => {
    expect(agencyFor("cron")).toBe("autonomous");
    expect(agencyFor("event")).toBe("autonomous");
    expect(agencyFor("loop")).toBe("autonomous");
  });

  it("makes NO claim when the record does not say", () => {
    // Over half this box's run history predates the trigger field. A guessed
    // provenance mark is worse than an absent one, so callers render nothing.
    expect(agencyFor(undefined)).toBeNull();
    expect(agencyFor(null)).toBeNull();
    expect(agencyFor("")).toBeNull();
    expect(agencyFor("something-new")).toBeNull();
  });
});
