import { describe, it, expect } from "vitest";
import { DEVELOPMENT_SCOPE, devScopes, orderScopes } from "./devMode";
import { taskScopeFor, scopeGroups, scopeKind, scopeLabel, scopeName } from "../components/chat/chatScope";
import { scopeLabel as taskScopeLabel, scopeHref, runTicketScope } from "./tasks";

// ADR 0014, phase 6: prove the claim rather than assert it.
//
// The property being tested is ABSENCE. In a normal build the Development scope
// and the surfaces that reach it are not in the bundle, so a regular user has no
// way to aim the app at its own source. That is a product capability boundary
// and a safety interlock, NOT a security boundary — someone with a shell on the
// box edits the source directly — so what these tests may claim is exactly
// this: a non-dev build does not OFFER it.
//
// Every rule takes devMode as an argument for this reason. `DEV_MODE` itself is
// frozen at import from `import.meta.env.VITE_DEV_MODE`, so a module-level
// constant could only ever be tested in whichever mode the test run happens to
// be built in — which is the one case that must not go untested.

describe("dev surfaces are absent from a non-dev build", () => {
  it("offers no dev-only scopes", () => {
    expect(devScopes(false)).toEqual([]);
    expect(devScopes(true)).toEqual([DEVELOPMENT_SCOPE]);
  });

  it("drops development from a scope list the server sent", () => {
    // A non-dev BUNDLE talking to a dev BACKEND is a real combination (the
    // public origin serves a built dist against the same API), and the bundle
    // is what decides what the operator can see.
    const fromServer = ["development", "workshop", "project:birds"];
    expect(orderScopes(fromServer, false)).toEqual(["workshop", "project:birds"]);
    expect(orderScopes(fromServer, false)).not.toContain(DEVELOPMENT_SCOPE);
  });

  it("has no Development group in the chat scope picker", () => {
    const kinds = scopeGroups(false).map((g) => g.kind);
    expect(kinds).not.toContain("development");
    expect(kinds[0]).toBe("workshop");
  });
});

describe("dev surfaces are present, and sort above Workshop, in a dev build", () => {
  it("sorts Development first in a flat scope list", () => {
    const ordered = orderScopes(["workshop", "project:birds", "development"], true);
    expect(ordered[0]).toBe(DEVELOPMENT_SCOPE);
    expect(ordered[1]).toBe("workshop");
  });

  it("sorts the Development group above Workshop in the picker", () => {
    const kinds = scopeGroups(true).map((g) => g.kind);
    expect(kinds[0]).toBe("development");
    expect(kinds[1]).toBe("workshop");
  });

  it("keeps the rest of the order stable and alphabetical below the roots", () => {
    expect(orderScopes(["project:zulu", "agent:alpha", "workshop"], true))
      .toEqual(["workshop", "agent:alpha", "project:zulu"]);
  });
});

describe("naming: development is its own kind, not a project", () => {
  it("reads as Development everywhere a scope is named", () => {
    expect(scopeKind(DEVELOPMENT_SCOPE)).toBe("development");
    expect(scopeLabel(DEVELOPMENT_SCOPE)).toBe("Development");
    expect(scopeName(DEVELOPMENT_SCOPE)).toBe("Development");
    expect(taskScopeLabel(DEVELOPMENT_SCOPE)).toBe("Development");
  });

  it("is never mistaken for the workshop root", () => {
    expect(scopeKind(DEVELOPMENT_SCOPE)).not.toBe(scopeKind("workshop"));
    expect(taskScopeLabel(DEVELOPMENT_SCOPE)).not.toBe(taskScopeLabel("workshop"));
  });

  it("has no dedicated page to link to", () => {
    expect(scopeHref(DEVELOPMENT_SCOPE)).toBeNull();
  });
});

describe("a run against the platform repo still finds its ticket", () => {
  // The queue was renamed, not relocated. Jobs stamped before the rename carry
  // project_slug "workshop"; their tickets are the same records.
  it("maps the pre-rename slug to development", () => {
    expect(runTicketScope({ project_slug: "workshop" })).toBe(DEVELOPMENT_SCOPE);
    expect(runTicketScope({ project_slug: "development" })).toBe(DEVELOPMENT_SCOPE);
    expect(runTicketScope({})).toBe(DEVELOPMENT_SCOPE);
  });

  it("leaves project runs alone", () => {
    expect(runTicketScope({ project_slug: "birds" })).toBe("project:birds");
  });
});

describe("the development scope owns a queue like any other work scope", () => {
  it("maps to a task scope, so its chat gets the ticket panel", () => {
    // It was omitted when the scope was added, so the scope holding the
    // platform's own 93 tickets (16 open) was the ONE work scope whose chat
    // showed none of them: no panel, no Tickets button. That single omission is
    // most of what made developer mode read as superficial on first use.
    expect(taskScopeFor("development")).toBe("development");
  });

  it("still maps the other work scopes", () => {
    expect(taskScopeFor("project:birds")).toBe("project:birds");
    expect(taskScopeFor("agent:triager")).toBe("agent:triager");
    expect(taskScopeFor("build:x")).toBe("build:x");
  });

  it("still returns null for a chat with no queue", () => {
    expect(taskScopeFor("general")).toBeNull();
    expect(taskScopeFor("budget")).toBeNull();
  });
});
