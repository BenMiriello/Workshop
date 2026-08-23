import { describe, it, expect } from "vitest";
import {
  DOCS_ROOT,
  ancestorsOf,
  baseName,
  childScopes,
  dirName,
  docLevel,
  encodeMemoryNode,
  isBranch,
  nodeForParams,
  notesInScope,
  paramsForNode,
  parentOf,
  parseMemoryNode,
  searchDocs,
  titleOfMarkdown,
  type DocRow,
  type MemoryNode,
  type NoteRow,
  type ScopeNode,
} from "./memoryTree";

const tree: ScopeNode[] = [
  { key: "workshop", parent: null, depth: 0 },
  { key: "project:birds", parent: "workshop", depth: 1 },
  { key: "agent:scout", parent: "project:birds", depth: 2 },
  { key: "development", parent: null, depth: 0 },
];

const docs: DocRow[] = [
  { path: "docs/README.md", title: "Read me", section: "" },
  { path: "docs/platform-redesign/entity-model.md", title: "Entity model", section: "platform-redesign" },
  { path: "docs/platform-redesign/deep/one.md", title: "One", section: "platform-redesign" },
  { path: "docs/decisions/0001-a.md", title: "Alpha", section: "decisions" },
  { path: "PROJECT.md", title: "Project", section: "" },
];

describe("node ids round-trip", () => {
  const cases: MemoryNode[] = [
    { kind: "root" },
    { kind: "docs" },
    { kind: "scope", scope: "workshop" },
    { kind: "scope", scope: "project:birds" },
    { kind: "note", scope: "project:birds", id: "ports-are-squatted-abc" },
    { kind: "docdir", path: "docs/platform-redesign" },
    { kind: "doc", path: "docs/platform-redesign/entity-model.md" },
  ];
  for (const node of cases) {
    it(`survives ${JSON.stringify(node)}`, () => {
      expect(parseMemoryNode(encodeMemoryNode(node))).toEqual(node);
    });
  }

  it("keeps a note's scope and id apart even though a scope key has colons", () => {
    const id = encodeMemoryNode({ kind: "note", scope: "agent:scout", id: "a-b-c" });
    expect(parseMemoryNode(id)).toEqual({ kind: "note", scope: "agent:scout", id: "a-b-c" });
  });

  it("refuses an id it did not write", () => {
    expect(parseMemoryNode("")).toBeNull();
    expect(parseMemoryNode("wat")).toBeNull();
    expect(parseMemoryNode("note:noseparator")).toBeNull();
    expect(parseMemoryNode("scope:")).toBeNull();
  });
});

describe("scope levels", () => {
  it("lists the roots at the top", () => {
    expect(childScopes(tree, null)).toEqual(["workshop", "development"]);
  });

  it("lists only the direct children of a scope", () => {
    expect(childScopes(tree, "workshop")).toEqual(["project:birds"]);
    expect(childScopes(tree, "project:birds")).toEqual(["agent:scout"]);
    expect(childScopes(tree, "agent:scout")).toEqual([]);
  });
});

describe("notesInScope", () => {
  const notes = [
    { id: "a", scope: "workshop", title: "A", hook: "" },
    { id: "b", scope: "project:birds", title: "B", hook: "" },
  ] as NoteRow[];

  // `GET /api/notes?scope=workshop` answers with the scope AND everything it
  // reaches, so a level has to narrow it or a child's note appears twice.
  it("keeps a reached child's note off its parent's level", () => {
    expect(notesInScope(notes, "workshop").map((n) => n.id)).toEqual(["a"]);
    expect(notesInScope(notes, "project:birds").map((n) => n.id)).toEqual(["b"]);
  });
});

describe("docLevel", () => {
  it("splits one level into directories then files", () => {
    const level = docLevel(docs, DOCS_ROOT);
    expect(level.dirs).toEqual(["docs/decisions", "docs/platform-redesign"]);
    expect(level.files.map((f) => f.path)).toEqual(["docs/README.md"]);
  });

  it("descends without repeating deeper files", () => {
    const level = docLevel(docs, "docs/platform-redesign");
    expect(level.dirs).toEqual(["docs/platform-redesign/deep"]);
    expect(level.files.map((f) => f.path)).toEqual([
      "docs/platform-redesign/entity-model.md",
    ]);
  });

  it("never picks up a file outside the directory it was asked about", () => {
    // PROJECT.md is repo-root markdown a doc ref can address, and it is not
    // under docs/, so it belongs to no level of this tree.
    const level = docLevel(docs, DOCS_ROOT);
    expect(level.files.some((f) => f.path === "PROJECT.md")).toBe(false);
  });

  it("sorts files by title, not by path", () => {
    const unsorted: DocRow[] = [
      { path: "docs/z.md", title: "Aardvark", section: "" },
      { path: "docs/a.md", title: "Zebra", section: "" },
    ];
    expect(docLevel(unsorted, DOCS_ROOT).files.map((f) => f.title)).toEqual([
      "Aardvark",
      "Zebra",
    ]);
  });
});

describe("path helpers", () => {
  it("splits a path", () => {
    expect(baseName("docs/a/b.md")).toBe("b.md");
    expect(dirName("docs/a/b.md")).toBe("docs/a");
    expect(baseName("b.md")).toBe("b.md");
    expect(dirName("b.md")).toBe("");
  });
});

describe("parentOf walks containment, not history", () => {
  it("takes a note up to its scope", () => {
    expect(parentOf({ kind: "note", scope: "project:birds", id: "x" }, tree)).toEqual({
      kind: "scope",
      scope: "project:birds",
    });
  });

  it("takes a scope up to its parent scope, and a root scope up to the tree root", () => {
    expect(parentOf({ kind: "scope", scope: "agent:scout" }, tree)).toEqual({
      kind: "scope",
      scope: "project:birds",
    });
    expect(parentOf({ kind: "scope", scope: "workshop" }, tree)).toEqual({ kind: "root" });
  });

  it("takes a doc up to its directory and a top-level doc up to Documents", () => {
    expect(parentOf({ kind: "doc", path: "docs/platform-redesign/x.md" }, tree)).toEqual({
      kind: "docdir",
      path: "docs/platform-redesign",
    });
    expect(parentOf({ kind: "doc", path: "docs/README.md" }, tree)).toEqual({ kind: "docs" });
  });

  it("stops at the root", () => {
    expect(parentOf({ kind: "root" }, tree)).toBeNull();
  });

  it("treats an unknown scope as a root rather than losing it", () => {
    expect(parentOf({ kind: "scope", scope: "app:nope" }, tree)).toEqual({ kind: "root" });
  });
});

describe("ancestorsOf", () => {
  it("reads root first, and excludes the node itself", () => {
    expect(ancestorsOf({ kind: "note", scope: "agent:scout", id: "x" }, tree)).toEqual([
      { kind: "root" },
      { kind: "scope", scope: "workshop" },
      { kind: "scope", scope: "project:birds" },
      { kind: "scope", scope: "agent:scout" },
    ]);
  });

  it("terminates on a cyclic scope tree instead of hanging the page", () => {
    const cyclic: ScopeNode[] = [
      { key: "a", parent: "b", depth: 0 },
      { key: "b", parent: "a", depth: 0 },
    ];
    expect(ancestorsOf({ kind: "scope", scope: "a" }, cyclic).length).toBeLessThan(5);
  });
});

describe("branch or destination", () => {
  it("knows which nodes contain things", () => {
    expect(isBranch({ kind: "root" })).toBe(true);
    expect(isBranch({ kind: "scope", scope: "workshop" })).toBe(true);
    expect(isBranch({ kind: "docs" })).toBe(true);
    expect(isBranch({ kind: "docdir", path: "docs/a" })).toBe(true);
    expect(isBranch({ kind: "note", scope: "workshop", id: "x" })).toBe(false);
    expect(isBranch({ kind: "doc", path: "docs/a.md" })).toBe(false);
  });
});

describe("URL addressing keeps the links refs.py already handed out", () => {
  it("selects a note by scope and id, never by a path", () => {
    expect(paramsForNode({ kind: "note", scope: "workshop", id: "ports-abc" })).toEqual({
      scope: "workshop",
      note: "ports-abc",
    });
  });

  it("selects a doc by its repo-relative path", () => {
    expect(paramsForNode({ kind: "doc", path: "docs/a.md" })).toEqual({ path: "docs/a.md" });
  });

  it("has nothing to select for a pure container", () => {
    expect(paramsForNode({ kind: "docs" })).toBeNull();
    expect(paramsForNode({ kind: "root" })).toBeNull();
  });

  it("reads back the live deep-link shapes", () => {
    expect(nodeForParams({ scope: "workshop", note: "ports-abc" })).toEqual({
      kind: "note",
      scope: "workshop",
      id: "ports-abc",
    });
    expect(nodeForParams({ path: "docs/a.md" })).toEqual({ kind: "doc", path: "docs/a.md" });
    expect(nodeForParams({ scope: "project:birds" })).toEqual({
      kind: "scope",
      scope: "project:birds",
    });
  });

  it("reads the legacy all-scopes filter as no selection", () => {
    expect(nodeForParams({ scope: "all" })).toBeNull();
    expect(nodeForParams({})).toBeNull();
  });

  it("prefers a doc path over a stale note pair", () => {
    expect(nodeForParams({ path: "docs/a.md", scope: "workshop", note: "x" })).toEqual({
      kind: "doc",
      path: "docs/a.md",
    });
  });
});

describe("searchDocs", () => {
  it("matches a title or a path, case-insensitively", () => {
    expect(searchDocs(docs, "entity").map((d) => d.path)).toEqual([
      "docs/platform-redesign/entity-model.md",
    ]);
    expect(searchDocs(docs, "DECISIONS").map((d) => d.path)).toEqual(["docs/decisions/0001-a.md"]);
  });

  it("matches nothing on an empty query rather than everything", () => {
    expect(searchDocs(docs, "   ")).toEqual([]);
  });
});

describe("titleOfMarkdown", () => {
  it("takes the first h1", () => {
    expect(titleOfMarkdown("# The title\n\nbody", "x.md")).toBe("The title");
  });

  it("skips front matter and blank lines to find it", () => {
    expect(titleOfMarkdown("\n\n#   Spaced   \nbody", "x.md")).toBe("Spaced");
  });

  it("ignores a deeper heading", () => {
    expect(titleOfMarkdown("## Not this\n# This", "x.md")).toBe("This");
  });

  it("falls back when there is no heading at all", () => {
    expect(titleOfMarkdown("just prose", "notes.md")).toBe("notes.md");
    expect(titleOfMarkdown("", "notes.md")).toBe("notes.md");
  });
});
