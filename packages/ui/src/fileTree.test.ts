import { describe, expect, it } from "vitest";
import {
  FILE_TREE_MAX_ROWS_PER_LEVEL,
  flattenTree,
  levelFailed,
  levelForget,
  levelLoading,
  levelReady,
  nodesById,
  toggleBranch,
  treeKeyIntent,
  type FileTreeLevels,
  type FileTreeNode,
  type FileTreeRow,
} from "./fileTree";

// The decidable half of the tree: the row list, the open set, and what happens
// to the levels already on screen when one of them fails. Everything else needs
// a browser and the repo has no DOM harness (frontend/CLAUDE.md).

const root: FileTreeNode = { id: "", label: "workspace", kind: "branch" };
const dir = (id: string, label = id): FileTreeNode => ({ id, label, kind: "branch" });
const file = (id: string, label = id): FileTreeNode => ({ id, label, kind: "leaf" });

/** src/ and README.md at the top; src/ holds lib/ and main.ts. */
function fixture(): FileTreeLevels {
  let levels: FileTreeLevels = {};
  levels = levelReady(levels, "", { nodes: [dir("src"), file("README.md")] });
  levels = levelReady(levels, "src", { nodes: [dir("src/lib"), file("src/main.ts")] });
  levels = levelReady(levels, "src/lib", { nodes: [file("src/lib/cx.ts")] });
  return levels;
}

const keys = (rows: FileTreeRow[]) => rows.map((r) => r.key);
const depths = (rows: FileTreeRow[]) => rows.map((r) => [r.key, r.depth] as const);

describe("flattenTree", () => {
  it("renders the root's children at depth 0 and never the root itself", () => {
    const rows = flattenTree(root, fixture(), new Set());
    expect(depths(rows)).toEqual([
      ["src", 0],
      ["README.md", 0],
    ]);
  });

  it("gives every row of an open tree a depth", () => {
    const rows = flattenTree(root, fixture(), new Set(["src", "src/lib"]));
    expect(depths(rows)).toEqual([
      ["src", 0],
      ["src/lib", 1],
      ["src/lib/cx.ts", 2],
      ["src/main.ts", 1],
      ["README.md", 0],
    ]);
  });

  it("marks which branches are open, and only branches", () => {
    const rows = flattenTree(root, fixture(), new Set(["src", "README.md"]));
    const open = rows.filter((r) => r.kind === "node" && r.open).map((r) => r.key);
    // README.md is in the open set but it is a leaf, so it is not open.
    expect(open).toEqual(["src"]);
  });

  it("returns no rows at all while the root level is loading or missing", () => {
    expect(flattenTree(root, {}, new Set())).toEqual([]);
    expect(flattenTree(root, levelLoading({}, ""), new Set())).toEqual([]);
  });

  it("returns no rows for an empty or failed ROOT level, because that is a region", () => {
    // Both are `StateBlock` cases in the component: nothing is on screen.
    expect(flattenTree(root, levelReady({}, "", { nodes: [] }), new Set())).toEqual([]);
    expect(flattenTree(root, levelFailed({}, "", "500"), new Set())).toEqual([]);
  });

  it("emits a loading row under an open branch whose level has not arrived", () => {
    const levels = levelReady({}, "", { nodes: [dir("src")] });
    const rows = flattenTree(root, levels, new Set(["src"]));
    expect(rows[1]).toMatchObject({ kind: "loading", parentId: "src", depth: 1 });
  });

  it("emits an empty row under an open branch with no children", () => {
    let levels = levelReady({}, "", { nodes: [dir("src")] });
    levels = levelReady(levels, "src", { nodes: [] });
    const rows = flattenTree(root, levels, new Set(["src"]));
    expect(rows[1]).toMatchObject({ kind: "empty", parentId: "src", depth: 1 });
  });

  it("reports what the source filtered out of a level", () => {
    let levels = levelReady({}, "", { nodes: [file("a")], filtered: [".git", "node_modules"] });
    levels = levelReady(levels, "src", { nodes: [] });
    const rows = flattenTree(root, levels, new Set());
    expect(rows[1]).toMatchObject({ kind: "filtered", names: [".git", "node_modules"], depth: 0 });
  });

  it("caps a level and reports the remainder as one overflow row", () => {
    const nodes = Array.from({ length: 12 }, (_, i) => file(`f${i}`));
    const levels = levelReady({}, "", { nodes });
    const rows = flattenTree(root, levels, new Set(), { maxRowsPerLevel: 5 });
    expect(rows).toHaveLength(6);
    expect(rows[5]).toMatchObject({ kind: "overflow", hidden: 7 });
  });

  it("does not cap a level that fits", () => {
    const rows = flattenTree(root, fixture(), new Set(["src"]));
    expect(rows.some((r) => r.kind === "overflow")).toBe(false);
    expect(FILE_TREE_MAX_ROWS_PER_LEVEL).toBeGreaterThan(4);
  });

  it("drops a repeated id, so a cyclic adapter terminates", () => {
    let levels = levelReady({}, "", { nodes: [dir("a")] });
    levels = levelReady(levels, "a", { nodes: [dir("a"), file("a/b")] });
    const rows = flattenTree(root, levels, new Set(["a"]));
    expect(keys(rows)).toEqual(["a", "a/b"]);
  });

  it("gives every row a unique key, which is what a virtualizer will need later", () => {
    const rows = flattenTree(root, fixture(), new Set(["src", "src/lib"]));
    expect(new Set(keys(rows)).size).toBe(rows.length);
  });
});

describe("toggleBranch", () => {
  it("opens a closed branch and closes an open one", () => {
    const open = toggleBranch(new Set(), "src");
    expect([...open]).toEqual(["src"]);
    expect([...toggleBranch(open, "src")]).toEqual([]);
  });

  it("leaves the other branches alone", () => {
    const open = toggleBranch(new Set(["a", "b"]), "c");
    expect([...open].sort()).toEqual(["a", "b", "c"]);
  });

  it("does not mutate the set it was given", () => {
    const before = new Set(["a"]);
    toggleBranch(before, "b");
    expect([...before]).toEqual(["a"]);
  });

  it("forces a direction when asked, so opening what is open is a no-op", () => {
    expect([...toggleBranch(new Set(["a"]), "a", true)]).toEqual(["a"]);
    expect([...toggleBranch(new Set(), "a", false)]).toEqual([]);
  });
});

describe("a level that fails to load", () => {
  it("does not disturb any other level", () => {
    const before = fixture();
    const after = levelFailed(before, "src/lib", "503 upstream unavailable");
    expect(after[""]).toBe(before[""]);
    expect(after["src"]).toBe(before["src"]);
    expect(after["src/lib"]).toEqual({ status: "error", message: "503 upstream unavailable" });
  });

  it("keeps the levels already open on screen, and reports itself as a row beside them", () => {
    const levels = levelFailed(fixture(), "src/lib", "503");
    const rows = flattenTree(root, levels, new Set(["src", "src/lib"]));
    // src's children are still listed; only src/lib's own level is replaced.
    expect(depths(rows)).toEqual([
      ["src", 0],
      ["src/lib", 1],
      ["src/lib::error", 2],
      ["src/main.ts", 1],
      ["README.md", 0],
    ]);
    expect(rows[2]).toMatchObject({ kind: "error", message: "503", parentId: "src/lib" });
  });

  it("hides its error row again when the branch is closed", () => {
    const levels = levelFailed(fixture(), "src/lib", "503");
    const rows = flattenTree(root, levels, new Set(["src"]));
    expect(rows.some((r) => r.kind === "error")).toBe(false);
  });

  it("goes back to loading on retry, then resolves", () => {
    const base = fixture();
    const failed = levelFailed(base, "src/lib", "503");
    const retrying = levelLoading(failed, "src/lib");
    expect(retrying["src/lib"]).toEqual({ status: "loading" });
    const done = levelReady(retrying, "src/lib", { nodes: [file("src/lib/cx.ts")] });
    expect(done["src/lib"]).toMatchObject({ status: "ready" });
    // The unrelated levels came through all three transitions untouched.
    expect(done["src"]).toBe(base["src"]);
  });

  it("forgets a level so the next open re-requests it", () => {
    const forgotten = levelForget(levelFailed(fixture(), "src/lib", "503"), "src/lib");
    expect("src/lib" in forgotten).toBe(false);
    expect(forgotten["src"]).toBeDefined();
    expect(levelForget(forgotten, "nope")).toBe(forgotten);
  });
});

describe("levelReady", () => {
  it("carries filtered only when there is something to report", () => {
    expect(levelReady({}, "", { nodes: [] })[""]).toEqual({ status: "ready", nodes: [] });
    expect(levelReady({}, "", { nodes: [], filtered: [] })[""]).toEqual({ status: "ready", nodes: [] });
    expect(levelReady({}, "", { nodes: [], filtered: [".git"] })[""]).toEqual({
      status: "ready",
      nodes: [],
      filtered: [".git"],
    });
  });
});

describe("nodesById", () => {
  it("indexes the root and every node any loaded level knows", () => {
    const byId = nodesById(root, fixture());
    expect([...byId.keys()].sort()).toEqual(["", "README.md", "src", "src/lib", "src/lib/cx.ts", "src/main.ts"]);
    expect(byId.get("src/lib")).toMatchObject({ kind: "branch" });
  });
});

describe("treeKeyIntent", () => {
  const rows = () => flattenTree(root, fixture(), new Set(["src"]));
  // src(0) / src/lib(1) / src/main.ts(1) / README.md(0)

  it("moves down and up", () => {
    expect(treeKeyIntent(rows(), "src", "ArrowDown")).toEqual({ type: "focus", id: "src/lib" });
    expect(treeKeyIntent(rows(), "src/main.ts", "ArrowUp")).toEqual({ type: "focus", id: "src/lib" });
  });

  it("stops at both ends rather than wrapping", () => {
    expect(treeKeyIntent(rows(), "src", "ArrowUp")).toBeNull();
    expect(treeKeyIntent(rows(), "README.md", "ArrowDown")).toBeNull();
  });

  it("starts somewhere sensible when nothing is focused", () => {
    expect(treeKeyIntent(rows(), null, "ArrowDown")).toEqual({ type: "focus", id: "src" });
    expect(treeKeyIntent(rows(), null, "ArrowUp")).toEqual({ type: "focus", id: "README.md" });
    expect(treeKeyIntent(rows(), null, "Enter")).toBeNull();
  });

  it("right opens a closed branch and then steps into it", () => {
    const closed = flattenTree(root, fixture(), new Set());
    expect(treeKeyIntent(closed, "src", "ArrowRight")).toEqual({ type: "open", id: "src" });
    expect(treeKeyIntent(rows(), "src", "ArrowRight")).toEqual({ type: "focus", id: "src/lib" });
  });

  it("right does nothing on a leaf", () => {
    expect(treeKeyIntent(rows(), "README.md", "ArrowRight")).toBeNull();
  });

  it("right does nothing on an open branch whose level is not on screen yet", () => {
    let levels = levelReady({}, "", { nodes: [dir("src")] });
    levels = levelLoading(levels, "src");
    const loading = flattenTree(root, levels, new Set(["src"]));
    expect(treeKeyIntent(loading, "src", "ArrowRight")).toBeNull();
  });

  it("left closes an open branch, then steps out to the parent", () => {
    expect(treeKeyIntent(rows(), "src", "ArrowLeft")).toEqual({ type: "close", id: "src" });
    expect(treeKeyIntent(rows(), "src/main.ts", "ArrowLeft")).toEqual({ type: "focus", id: "src" });
  });

  it("left at the top level has nowhere to go", () => {
    expect(treeKeyIntent(rows(), "README.md", "ArrowLeft")).toBeNull();
  });

  it("enter selects whatever is focused, branch or leaf", () => {
    expect(treeKeyIntent(rows(), "README.md", "Enter")).toEqual({ type: "select", id: "README.md" });
    expect(treeKeyIntent(rows(), "src", "Enter")).toEqual({ type: "select", id: "src" });
  });

  it("claims no other key, so the page keeps its own shortcuts", () => {
    expect(treeKeyIntent(rows(), "src", "a")).toBeNull();
    expect(treeKeyIntent(rows(), "src", "Tab")).toBeNull();
    expect(treeKeyIntent(rows(), "src", "PageDown")).toBeNull();
  });

  it("ignores the rows that are not nodes", () => {
    const levels = levelFailed(fixture(), "src/lib", "503");
    const withAsides = flattenTree(root, levels, new Set(["src", "src/lib"]));
    expect(withAsides.some((r) => r.kind === "error")).toBe(true);
    // Down from the failed branch skips its error line and lands on the next file.
    expect(treeKeyIntent(withAsides, "src/lib", "ArrowDown")).toEqual({
      type: "focus",
      id: "src/main.ts",
    });
  });

  it("has nothing to say about an empty tree", () => {
    expect(treeKeyIntent([], null, "ArrowDown")).toBeNull();
  });
});
