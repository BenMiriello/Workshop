import { describe, expect, it } from "vitest";
import {
  DIFF_LINE_CAP,
  changeStatusKind,
  changeStatusLabel,
  changesUnavailable,
  clipDiff,
  decisionRefusal,
  diffLineKind,
  diffTotals,
  fileStat,
  fileStatusLabel,
  gateKind,
  isDeletedFile,
  isOpenChange,
  riskSignals,
  sizeBand,
  startsExpanded,
  ticketRef,
  ticketTargetFromHref,
  touchesManifest,
  touchesMigration,
} from "./changeReview";

// A real one-file patch as `worktrees.diff_branch` hands it over: the
// `diff --git` header, the index line, BOTH file headers, a hunk header and
// four content lines.
const PATCH = [
  "diff --git a/backend/services/changes.py b/backend/services/changes.py",
  "index 1111111..2222222 100644",
  "--- a/backend/services/changes.py",
  "+++ b/backend/services/changes.py",
  "@@ -10,5 +10,6 @@ def open_change(project_slug):",
  " context line",
  "-removed line",
  "+added line one",
  "+added line two",
  " trailing context",
].join("\n");

describe("diffLineKind", () => {
  it("reads the file headers as meta, not as one add and one remove", () => {
    // The defect this exists to stop: `+++`/`---` start with the same
    // characters as a content line, so a naive counter reports every file as
    // +1/-1 more than it is.
    expect(diffLineKind("--- a/x.py")).toBe("meta");
    expect(diffLineKind("+++ b/x.py")).toBe("meta");
    expect(diffLineKind("+added")).toBe("add");
    expect(diffLineKind("-gone")).toBe("del");
    expect(diffLineKind("@@ -1,2 +1,3 @@")).toBe("hunk");
    expect(diffLineKind(" untouched")).toBe("context");
    expect(diffLineKind("diff --git a/x b/x")).toBe("meta");
    expect(diffLineKind("new file mode 100644")).toBe("meta");
  });
});

describe("fileStat", () => {
  it("counts a real patch's adds and removes", () => {
    const s = fileStat(PATCH);
    expect(s.added).toBe(2);
    expect(s.removed).toBe(1);
    expect(s.binary).toBe(false);
    expect(s.lines).toBe(10);
  });

  it("is zero for an empty patch rather than NaN", () => {
    expect(fileStat("")).toEqual({ added: 0, removed: 0, lines: 0, binary: false });
  });

  it("flags a binary patch, whose line counts mean nothing", () => {
    const s = fileStat("diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ");
    expect(s.binary).toBe(true);
    expect(s.added).toBe(0);
  });
});

describe("diffTotals", () => {
  it("adds up across files", () => {
    const totals = diffTotals([
      { path: "a.py", status: "M", diff: PATCH },
      { path: "b.py", status: "A", diff: "+++ b/b.py\n+one\n+two\n+three" },
    ]);
    expect(totals).toEqual({ files: 2, added: 5, removed: 1, changed: 6 });
  });
});

describe("sizeBand", () => {
  it("bands by changed lines, because reviewer accuracy falls off with size", () => {
    expect(sizeBand(0)).toBe("small");
    expect(sizeBand(50)).toBe("small");
    expect(sizeBand(51)).toBe("medium");
    expect(sizeBand(200)).toBe("medium");
    expect(sizeBand(201)).toBe("large");
    expect(sizeBand(600)).toBe("large");
    expect(sizeBand(601)).toBe("huge");
  });
});

describe("touchesManifest / touchesMigration", () => {
  it("catches the lockfiles and manifests of every stack in this repo", () => {
    for (const p of [
      "package.json", "frontend/package-lock.json", "pnpm-lock.yaml", "yarn.lock",
      "backend/requirements.txt", "backend/requirements-dev.txt", "pyproject.toml",
      "poetry.lock", "Pipfile", "Pipfile.lock", "Cargo.toml", "Cargo.lock",
      "go.mod", "go.sum", "Gemfile.lock",
    ]) {
      expect(touchesManifest(p), p).toBe(true);
    }
  });

  it("does not call an ordinary source file a manifest", () => {
    expect(touchesManifest("frontend/src/lib/packages.ts")).toBe(false);
    expect(touchesManifest("docs/package-notes.md")).toBe(false);
  });

  it("catches migrations by directory and by numbered filename", () => {
    expect(touchesMigration("backend/migrations/0007_add_change.sql")).toBe(true);
    expect(touchesMigration("backend/db/alembic/versions/ab12.py")).toBe(true);
    expect(touchesMigration("db/schema.sql")).toBe(true);
    expect(touchesMigration("backend/services/changes.py")).toBe(false);
  });
});

describe("riskSignals", () => {
  it("returns nothing for a small, ordinary source-only diff", () => {
    expect(riskSignals([{ path: "backend/services/changes.py", status: "M", diff: PATCH }]))
      .toEqual([]);
  });

  it("names dependencies, migrations, deletions and size", () => {
    const big = ["+++ b/x", ...Array.from({ length: 700 }, (_, i) => `+line ${i}`)].join("\n");
    const ids = riskSignals([
      { path: "frontend/package-lock.json", status: "M", diff: "+++ b/p\n+  \"left-pad\": \"1.0.0\"" },
      { path: "backend/migrations/0007_x.sql", status: "A", diff: "+++ b/m\n+ALTER TABLE" },
      { path: "backend/old.py", status: "D", diff: "--- a/backend/old.py\n-gone" },
      { path: "frontend/src/big.ts", status: "M", diff: big },
    ]).map((s) => s.id);
    expect(ids).toEqual(["dependencies", "migration", "deletions", "size"]);
  });

  it("says how many files were deleted, and names the first few", () => {
    const deleted = riskSignals(
      ["a", "b", "c", "d", "e"].map((p) => ({ path: `${p}.py`, status: "D", diff: `--- a/${p}.py\n-x` })),
    ).find((s) => s.id === "deletions");
    expect(deleted?.label).toBe("5 files deleted");
    expect(deleted?.detail).toBe("a.py, b.py, c.py and 2 more");
  });
});

describe("startsExpanded", () => {
  it("opens the patch of a small change and leaves a big one closed", () => {
    const small = { added: 3, removed: 1, lines: 12, binary: false };
    expect(startsExpanded(1, small)).toBe(true);
    expect(startsExpanded(6, small)).toBe(true);
    expect(startsExpanded(7, small)).toBe(false);
    expect(startsExpanded(1, { ...small, lines: DIFF_LINE_CAP + 1 })).toBe(false);
    expect(startsExpanded(1, { ...small, binary: true })).toBe(false);
  });
});

describe("clipDiff", () => {
  it("leaves a short patch alone", () => {
    expect(clipDiff(PATCH)).toEqual({ text: PATCH, clipped: 0 });
  });

  it("cuts a long one and says how much it cut", () => {
    const long = Array.from({ length: 30 }, (_, i) => `+line ${i}`).join("\n");
    const out = clipDiff(long, 10);
    expect(out.text.split("\n")).toHaveLength(10);
    expect(out.clipped).toBe(20);
  });
});

describe("decisionRefusal", () => {
  it("shows a 409's own sentence, which shortHttpError would have dropped", () => {
    // The merge-conflict answer from routes/tasks_all.py is well over
    // ERROR_BODY_CAP (200), so the standard formatter replaces it with
    // "409 Conflict" and the operator is told nothing about the branch.
    const reason = "this work cannot be merged, so it is not accepted: " + "CONFLICT (content): Merge conflict in backend/services/changes.py. ".repeat(4)
      + "Send it back with a note, or resolve the branch first.";
    const line = decisionRefusal(409, "Conflict", JSON.stringify({ detail: reason }));
    expect(line).toBe(reason);
    expect(line.length).toBeGreaterThan(200);
  });

  it("reads the ADR 0023 object shape too", () => {
    const body = JSON.stringify({ detail: { code: "conflict", message: "branch diverged" } });
    expect(decisionRefusal(409, "Conflict", body)).toBe("branch diverged");
  });

  it("falls back to the short line for every other status", () => {
    expect(decisionRefusal(404, "Not Found", "")).toBe("404 Not found.");
    expect(decisionRefusal(409, "Conflict", "<html>gateway</html>")).toBe("409 Conflict");
  });
});

describe("ticketTargetFromHref", () => {
  it("reads the scope out of the resolver's href, which is the only place it exists", () => {
    expect(ticketTargetFromHref("/tickets/workshop/abc123")).toEqual({ scope: "workshop", id: "abc123" });
    expect(ticketTargetFromHref("/tickets/project%3Afinance/t-9")).toEqual({
      scope: "project:finance", id: "t-9",
    });
  });

  it("refuses anything that is not a ticket href rather than guessing", () => {
    expect(ticketTargetFromHref("/runs/abc")).toBeNull();
    expect(ticketTargetFromHref("/tickets/workshop")).toBeNull();
    expect(ticketTargetFromHref("")).toBeNull();
    expect(ticketTargetFromHref(null)).toBeNull();
  });

  it("round-trips the ref format the resolver takes", () => {
    expect(ticketRef("abc123")).toBe("workshop:ticket/abc123");
  });
});

describe("status vocabulary", () => {
  it("maps every change status to a kind and a readable label", () => {
    expect(changeStatusKind("ready")).toBe("warn");
    expect(changeStatusKind("merged")).toBe("ok");
    expect(changeStatusKind("draft")).toBe("stopped");
    expect(changeStatusKind("something-new")).toBe("stopped");
    expect(changeStatusLabel("changes_requested")).toBe("changes requested");
    expect(changeStatusLabel("something-new")).toBe("something-new");
  });

  it("calls anything unmerged and unclosed open", () => {
    expect(isOpenChange({ status: "ready" })).toBe(true);
    expect(isOpenChange({ status: "merged" })).toBe(false);
    expect(isOpenChange({ status: "closed" })).toBe(false);
  });

  it("tells an absent store from a failed load", () => {
    // 503 is `ChangesUnavailable`: no relational store, so there are no change
    // records at all. Showing "Couldn't load, retry" for that is a page that
    // looks broken forever.
    expect(changesUnavailable(503)).toBe(true);
    expect(changesUnavailable(500)).toBe(false);
    expect(changesUnavailable(200)).toBe(false);
  });

  it("never guesses a gate green", () => {
    expect(gateKind("pass")).toBe("ok");
    expect(gateKind("FAILED")).toBe("danger");
    expect(gateKind("running")).toBe("running");
    expect(gateKind("skipped")).toBe("stopped");
    expect(gateKind("who knows")).toBe("warn");
    expect(gateKind(undefined)).toBe("warn");
  });

  it("labels git's status letters, similarity score and all", () => {
    expect(fileStatusLabel("A")).toBe("added");
    expect(fileStatusLabel("R100")).toBe("renamed");
    expect(fileStatusLabel("D")).toBe("deleted");
    expect(isDeletedFile("D")).toBe(true);
    expect(isDeletedFile("M")).toBe(false);
  });
});
