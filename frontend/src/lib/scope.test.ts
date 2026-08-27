import { describe, it, expect } from "vitest";
import { scopeLabel, scopeHref, orderScopes, scopeForContext, scopeHasQueue } from "./scope";
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
    ["agent:scribe", "agent:scribe"],
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
    expect(taskScopeFor("agent:scribe")).toBe("agent:scribe");
  });

  it("never opens a queue for an unrecognised key", () => {
    expect(taskScopeFor("zzz-not-a-scope")).toBeNull();
    expect(taskScopeFor("role:maker")).toBeNull();
  });

  it("agrees with scopeForContext wherever it answers", () => {
    for (const key of ["development", "project:birds", "agent:scribe"]) {
      expect(taskScopeFor(key)).toBe(scopeForContext(key));
    }
  });

  // app-is-the-entity-2026-08-20.md sec 2.2: the backend removed the blanket
  // `_QUEUELESS_PREFIXES = ("app:",)` exclusion because a linked app owns a
  // ticket queue now. `scopeHasQueue`/`taskScopeFor` mirror that with a
  // caller-supplied set of linked app slugs, since neither is allowed to
  // fetch it itself. Fixture app ids here are deliberately made up (not the
  // ones the tests above use) -- this file's own literals are watched by
  // scripts/check_no_app_names.py, and the rule under test does not care
  // which app it is.
  it("opens a queue for an app scope once its link is known", () => {
    const linked = new Set(["sample-app"]);
    expect(scopeHasQueue("app:sample-app", linked)).toBe(true);
    expect(taskScopeFor("app:sample-app", linked)).toBe("app:sample-app");
    expect(taskScopeFor("sample-app", linked)).toBe("app:sample-app");
  });

  it("still gives no queue to an app that is not linked", () => {
    const linked = new Set(["sample-app"]);
    expect(scopeHasQueue("app:other-app", linked)).toBe(false);
    expect(taskScopeFor("app:other-app", linked)).toBeNull();
  });

  it("defaults to no queue while the link set has not loaded yet", () => {
    expect(scopeHasQueue("app:sample-app", null)).toBe(false);
    expect(scopeHasQueue("app:sample-app", undefined)).toBe(false);
  });

  it("the workshop root never gets a queue, linked or not", () => {
    expect(scopeHasQueue("workshop", new Set(["workshop"]))).toBe(false);
  });
});

// An app scope had NO case in `scopeLabel`, so it fell through to the raw kind
// and the Memory tree read "app: widgets" beside "Project: widgets" and
// "Agent: sweeper" -- three capitalisations in one list. The backend's
// `scope_tree.label()` has carried `"app": "App"` all along, so this was the
// client half drifting from the server, which is exactly what the role tests
// above were written to catch for a different kind.
describe("an app scope is named like every other scope", () => {
  it("capitalises the kind instead of leaking it", () => {
    expect(scopeLabel("app:widgets")).toBe("App: widgets");
  });

  it("matches the label the backend renders for the same key", () => {
    // backend services/scope_tree.label(): {"app": "App", ...}
    expect(scopeLabel("app:gizmo")).toBe("App: gizmo");
  });

  it("reads consistently beside the project and agent kinds", () => {
    const labels = ["app:widgets", "project:widgets", "agent:sweeper"].map(scopeLabel);
    for (const l of labels) expect(l[0]).toBe(l[0].toUpperCase());
  });

  it("does not label the deleted `build:` kind", () => {
    // `build:<slug>` is a deleted spelling (chatScope.ts, and the
    // "does not recognise the deleted `build:` kind" test above), so the branch
    // that rendered it "Build" named something that can no longer arrive here.
    expect(scopeLabel("build:solo")).toBe("build: solo");
  });
});
