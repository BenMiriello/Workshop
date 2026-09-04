// What a chat's contextKey means, now that the key alone cannot say it.
//
// `deriveMode` used to read a `build:` prefix. That prefix was a second spelling
// of `agent:<slug>` and is deleted, so the mode has to come from the agent
// record instead. These pin the replacement rule, because it is a rule with two
// halves and dropping either one produces a plausible-looking bug: a bare
// `role === "maker"` check sends a project-less maker to a work pane for a
// directory that does not exist.
import { describe, expect, it } from "vitest";
import { agentSlug, deriveMode, scopeKind, scopeLabel } from "./chatScope";

const maker = { role: "maker", project_slug: "atlas" };

describe("agentSlug", () => {
  it("reads the slug out of the one agent key there is", () => {
    expect(agentSlug("agent:atlas-builder")).toBe("atlas-builder");
  });

  it("does not answer for the deleted `build:` spelling", () => {
    expect(agentSlug("build:atlas-builder")).toBeNull();
  });

  it("treats a bare `agent:` as no agent rather than an empty-named one", () => {
    expect(agentSlug("agent:")).toBeNull();
  });

  it("answers null for every key that is not an agent", () => {
    for (const k of ["general", "workshop", "development", "project:atlas", "app:ledger"]) {
      expect(agentSlug(k)).toBeNull();
    }
  });
});

describe("deriveMode", () => {
  it("is build for a maker with a project, which is the only agent with a tree", () => {
    expect(deriveMode("agent:atlas-builder", maker)).toBe("build");
  });

  it("is data for a maker with no project link", () => {
    // The half a bare role check would get wrong. A maker's tree IS its linked
    // project, so without one there is no work pane to send it to.
    expect(deriveMode("agent:solo", { role: "maker", project_slug: null })).toBe("data");
  });

  it("is data for every other role, project link or not", () => {
    for (const role of ["reviewer", "triager", "keeper", "broker", null]) {
      expect(deriveMode("agent:x", { role, project_slug: "atlas" })).toBe("data");
    }
  });

  it("is data while the agent record is still unknown", () => {
    // The honest answer during the fetch: the transcript and composer work, and
    // the build affordances arrive when the agent says they should.
    expect(deriveMode("agent:atlas-builder")).toBe("data");
    expect(deriveMode("agent:atlas-builder", null)).toBe("data");
  });

  it("is data for a non-agent key even when handed a maker record", () => {
    // The key decides WHICH agent; a project chat is not an agent chat however
    // the caller answers.
    expect(deriveMode("project:atlas", maker)).toBe("data");
    expect(deriveMode("general", maker)).toBe("data");
  });

  it("never reads the deleted `build:` prefix", () => {
    expect(deriveMode("build:atlas-builder", maker)).toBe("data");
  });
});

describe("naming a context key", () => {
  it("has one agent kind, and `build:` is not it", () => {
    expect(scopeKind("agent:scribe")).toBe("agent");
    expect(scopeKind("build:scribe")).toBe("other");
    expect(scopeLabel("agent:scribe")).toBe("Agent · scribe");
  });
});
