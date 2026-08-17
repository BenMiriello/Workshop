import { describe, it, expect } from "vitest";
import { scopeLabel, scopeHref, orderScopes } from "./scope";

// `role:<r>` is the SECOND AXIS of the knowledge store (backend scope_tree:
// "TWO AXES, NOT ONE"). It reaches every reviewer's prompt through
// `scope_tree.govern`, but `routes/notes_all._scope_roots` did not enumerate it,
// so `shared/knowledge/roles/reviewer/review-evidence.md` could not be listed,
// opened or edited from the Notes page. B4 of
// docs/platform-redesign/memory-consolidation-2026-08-16.md surfaces it; the
// picker gets the key from the server, so the only client-side part is naming
// it. This does NOT rename the axis: that is the vocabulary thread's job (sec 6
// of the same doc).
describe("a role scope is named like every other scope", () => {
  it("labels the kind rather than leaking the raw key", () => {
    expect(scopeLabel("role:reviewer")).toBe("Role: reviewer");
  });

  it("matches the label the backend renders for the same key", () => {
    // backend services/scope_tree.label(): `f"{kind_label}: {scope.slug}"`.
    expect(scopeLabel("role:maker")).toBe("Role: maker");
  });

  it("is not confused with the workshop root it sits beside", () => {
    expect(scopeLabel("role:reviewer")).not.toBe(scopeLabel("workshop"));
  });

  it("has no dedicated page to link to", () => {
    // Roles have no detail route, unlike projects and agents.
    expect(scopeHref("role:reviewer")).toBeNull();
  });

  it("sorts after the two fixed scopes in a picker", () => {
    expect(orderScopes(["role:reviewer", "workshop", "development"], true))
      .toEqual(["development", "workshop", "role:reviewer"]);
  });
});
