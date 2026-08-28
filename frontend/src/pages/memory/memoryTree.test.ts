import { describe, it, expect } from "vitest";
import {
  DOCS_ROOT,
  ancestorsOf,
  drillAncestorsFor,
  memoryFileNode,
  nodeLabel,
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
  scopeForNewNote,
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

describe("the kit's vocabulary", () => {
  it("names every branch a trail can contain, without another request", () => {
    expect(nodeLabel({ kind: "root" })).toBe("Memory");
    expect(nodeLabel({ kind: "docs" })).toBe("Documents");
    expect(nodeLabel({ kind: "docdir", path: "docs/platform-redesign" })).toBe("platform-redesign");
    expect(nodeLabel({ kind: "scope", scope: "project:birds" })).toBeTruthy();
  });

  it("labels a leaf from its address, which the level builder then overrides", () => {
    expect(nodeLabel({ kind: "note", scope: "workshop", id: "how-to" })).toBe("how-to");
    expect(nodeLabel({ kind: "doc", path: "docs/a/b.md" })).toBe("b.md");
  });

  it("carries the node's own id, so a row key is still an address", () => {
    const node = memoryFileNode({ kind: "note", scope: "workshop", id: "how-to" });
    expect(parseMemoryNode(node.id)).toEqual({ kind: "note", scope: "workshop", id: "how-to" });
    expect(node.kind).toBe("leaf");
  });

  it("marks containers as branches and destinations as leaves", () => {
    expect(memoryFileNode({ kind: "root" }).kind).toBe("branch");
    expect(memoryFileNode({ kind: "scope", scope: "workshop" }).kind).toBe("branch");
    expect(memoryFileNode({ kind: "docdir", path: "docs/a" }).kind).toBe("branch");
    expect(memoryFileNode({ kind: "doc", path: "docs/a.md" }).kind).toBe("leaf");
  });

  it("hints a scope apart from a folder, so the two halves of the tree read apart", () => {
    expect(memoryFileNode({ kind: "scope", scope: "workshop" }).icon).toBe("scope");
    expect(memoryFileNode({ kind: "root" }).icon).toBe("scope");
    expect(memoryFileNode({ kind: "docs" }).icon).toBe("folder");
    expect(memoryFileNode({ kind: "docdir", path: "docs/a" }).icon).toBe("folder");
    expect(memoryFileNode({ kind: "doc", path: "docs/a.md" }).icon).toBe("doc");
  });
});

describe("drillAncestorsFor is the phone's trail", () => {
  it("drops the root, which a trail always starts with anyway", () => {
    expect(drillAncestorsFor({ kind: "note", scope: "agent:scout", id: "x" }, tree).map((n) => n.id))
      .toEqual(["scope:workshop", "scope:project:birds", "scope:agent:scout"]);
  });

  it("ends on the level the document is IN, which is where Up lands", () => {
    const trail = drillAncestorsFor({ kind: "doc", path: "docs/platform-redesign/deep/one.md" }, tree);
    expect(trail.map((n) => n.id)).toEqual([
      "docs",
      "dir:docs/platform-redesign",
      "dir:docs/platform-redesign/deep",
    ]);
    expect(trail[trail.length - 1].label).toBe("deep");
  });

  it("is empty for a document directly under the root's own children", () => {
    expect(drillAncestorsFor({ kind: "scope", scope: "workshop" }, tree)).toEqual([]);
  });

  it("hands the kit branches only, since a leaf cannot be a level", () => {
    const trail = drillAncestorsFor({ kind: "doc", path: "docs/decisions/0001-a.md" }, tree);
    expect(trail.every((n) => n.kind === "branch")).toBe(true);
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

  // The two hrefs services/refs.py hands out, verbatim, walked the whole way a
  // phone walks them: the query selects a document, and Up out of that document
  // lands on the level it is IN, on the first press, without having drilled
  // there. That is the one behaviour the fold onto the kit's FileDrill changed
  // the mechanics of, so it is pinned here rather than assumed.
  const readHref = (href: string) => {
    const q = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    return nodeForParams({ scope: q.get("scope"), note: q.get("note"), path: q.get("path") });
  };

  it("opens /memory?scope=&note= and walks up to that note's scope", () => {
    const node = readHref("/memory?scope=agent:scout&note=ports-abc");
    expect(node).toEqual({ kind: "note", scope: "agent:scout", id: "ports-abc" });
    const trail = drillAncestorsFor(node!, tree);
    expect(trail[trail.length - 1].id).toBe("scope:agent:scout");
  });

  it("opens /memory?tab=docs&path= and walks up to that document's folder", () => {
    const node = readHref("/memory?tab=docs&path=docs/platform-redesign/deep/one.md");
    expect(node).toEqual({ kind: "doc", path: "docs/platform-redesign/deep/one.md" });
    const trail = drillAncestorsFor(node!, tree);
    expect(trail[trail.length - 1].id).toBe("dir:docs/platform-redesign/deep");
  });

  it("walks up to Documents from a doc sitting at the top of docs/", () => {
    const node = readHref("/memory?tab=docs&path=docs/README.md");
    const trail = drillAncestorsFor(node!, tree);
    expect(trail.map((n) => n.id)).toEqual(["docs"]);
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

// ---- Where a new note lands (2026-08-27, MEM-17) ---------------------------

describe("scopeForNewNote", () => {
  it("files into the scope of the note you are reading", () => {
    expect(scopeForNewNote({ kind: "note", scope: "project:birds", id: "x" }, "workshop"))
      .toBe("project:birds");
  });

  it("files into the scope you have selected", () => {
    expect(scopeForNewNote({ kind: "scope", scope: "development" }, "workshop"))
      .toBe("development");
  });

  it("falls back rather than inventing a scope from a document path", () => {
    // docs/ is a committed directory, not a scope: a note cannot be filed there.
    expect(scopeForNewNote({ kind: "doc", path: "docs/a/b.md" }, "workshop")).toBe("workshop");
    expect(scopeForNewNote({ kind: "docdir", path: "docs/a" }, "workshop")).toBe("workshop");
    expect(scopeForNewNote({ kind: "docs" }, "workshop")).toBe("workshop");
  });

  it("falls back at the root and with nothing selected", () => {
    expect(scopeForNewNote({ kind: "root" }, "workshop")).toBe("workshop");
    expect(scopeForNewNote(null, "workshop")).toBe("workshop");
  });

  it("uses the caller's fallback, not a hardcoded one", () => {
    expect(scopeForNewNote(null, "development")).toBe("development");
  });
});
