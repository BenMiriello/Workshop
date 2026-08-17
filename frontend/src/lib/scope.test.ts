import { describe, it, expect } from "vitest";
import { scopeLabel, scopeHref, orderScopes, scopeForContext } from "./scope";
import { taskScopeFor } from "../components/chat/chatScope";

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

// One context_key -> scope mapping, matching `scope_tree.scope_for_context`
// (T3 of docs/platform-redesign/coherence-overhaul-2026-08-16.md). There used
// to be five copies on the backend and one here, and they disagreed:
// `development` came out of `chat_store` as `app:development`, an address
// nothing else recognises, and it was stamped onto 27 live threads.
//
// The table below is the same table as `backend/tests/test_one_scope_mapping.py`
// `KEY_FORMS`, minus the two rows the client genuinely cannot answer (see the
// comment on `scopeForContext`).
describe("context_key resolves to one scope", () => {
  const forms: [string, string][] = [
    ["", "workshop"],
    ["general", "workshop"],
    ["workshop", "workshop"],
    ["new-project", "workshop"],
    ["development", "development"],
    ["project:birds", "project:birds"],
    ["app:budget", "app:budget"],
    ["agent:masini", "agent:masini"],
    ["budget", "app:budget"],
    ["role:maker", "workshop"],
    ["project:", "workshop"],
  ];

  for (const [key, scope] of forms) {
    it(`maps ${JSON.stringify(key)} to ${scope}`, () => {
      expect(scopeForContext(key)).toBe(scope);
    });
  }

  it("treats a missing key as the unbound chat", () => {
    expect(scopeForContext(null)).toBe("workshop");
    expect(scopeForContext(undefined)).toBe("workshop");
  });

  it("never yields the address that damaged 27 threads", () => {
    for (const [key] of forms) expect(scopeForContext(key)).not.toBe("app:development");
    expect(scopeForContext("development")).toBe("development");
  });

  it("does not recognise the deleted `build:` kind", () => {
    // It used to resolve to `agent:<slug>`, which is how the two spellings were
    // kept alive side by side. The spelling is deleted and the stored data is
    // migrated, so an unknown kind now falls back like any other.
    expect(scopeForContext("build:solo")).toBe("workshop");
  });
});

describe("which scopes a chat gets a task queue for", () => {
  it("gives no queue to the root or to an app dock", () => {
    // Showing a general chat the root backlog would make every chat in the
    // system look like it owned the same work.
    expect(taskScopeFor("general")).toBeNull();
    expect(taskScopeFor("workshop")).toBeNull();
    expect(taskScopeFor("budget")).toBeNull();
    expect(taskScopeFor("app:budget")).toBeNull();
  });

  it("gives a work scope its own queue, under its scope key", () => {
    expect(taskScopeFor("development")).toBe("development");
    expect(taskScopeFor("project:birds")).toBe("project:birds");
    expect(taskScopeFor("agent:masini")).toBe("agent:masini");
  });

  it("never opens a queue for an unrecognised key", () => {
    expect(taskScopeFor("zzz-not-a-scope")).toBeNull();
    expect(taskScopeFor("role:maker")).toBeNull();
  });

  it("agrees with scopeForContext wherever it answers", () => {
    for (const key of ["development", "project:birds", "agent:masini"]) {
      expect(taskScopeFor(key)).toBe(scopeForContext(key));
    }
  });
});
