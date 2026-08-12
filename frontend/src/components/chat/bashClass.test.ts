import { describe, expect, it } from "vitest";
import { bashClass, splitShellOps } from "./bashClass";

describe("splitShellOps", () => {
  it("splits on the real operators", () => {
    expect(splitShellOps("ls && wc -l x")).toEqual(["ls ", " wc -l x"]);
    expect(splitShellOps("a; b | c")).toEqual(["a", " b ", " c"]);
  });

  it("does not split inside quotes", () => {
    // The two commonest shapes in this transcript. A naive split tears both
    // apart and they end up unclassified, which is how the commands that most
    // deserved merging became the ones that never did.
    expect(splitShellOps(`grep -n "a\\|b" f`)).toEqual([`grep -n "a\\|b" f`]);
    expect(splitShellOps("sed -n '1,5p;9,12p' f")).toEqual(["sed -n '1,5p;9,12p' f"]);
  });
});

describe("bashClass: reading", () => {
  it("classifies the everyday read shapes", () => {
    expect(bashClass("sed -n '1,120p' src/App.tsx")).toBe("read");
    expect(bashClass("cat package.json")).toBe("read");
    expect(bashClass("grep -rn 'foo' src/")).toBe("grep");
    expect(bashClass("ls frontend/src/pages/")).toBe("list");
    expect(bashClass("find . -name '*.ts'")).toBe("list");
    expect(bashClass("wc -l a b c")).toBe("inspect");
  });

  it("names the act by its FIRST stage, not its last", () => {
    // `grep ... | head` is a search. Filing it under "read" because it ends in
    // head would put it beside the wrong things.
    expect(bashClass("grep -n 'x' f | head -30")).toBe("grep");
    expect(bashClass("ls -la | wc -l")).toBe("list");
  });

  it("sees through a cd prefix and a wrapper", () => {
    expect(bashClass("cd frontend && ls src")).toBe("list");
    expect(bashClass("timeout 10 cat f")).toBe("read");
  });

  it("allows redirects that provably mutate nothing", () => {
    expect(bashClass("ls .runtime/ 2>/dev/null")).toBe("list");
    expect(bashClass("grep -n x f 2>&1")).toBe("grep");
  });

  it("reads git's read-only subcommands", () => {
    expect(bashClass("git status --short")).toBe("inspect");
    expect(bashClass("git log --oneline -5")).toBe("inspect");
    expect(bashClass("git diff --stat")).toBe("inspect");
  });
});

describe("bashClass: everything it must refuse", () => {
  it("refuses writes and destructive commands", () => {
    for (const cmd of [
      "rm -rf important_dir",
      "mv a b",
      "cp a b",
      "mkdir -p x",
      "chmod 600 f",
      "npm install",
      "git commit -m 'x'",
      "git checkout main",
      "git branch -D old",
      "systemctl --user restart workshop-backend",
    ]) {
      expect(bashClass(cmd), cmd).toBeNull();
    }
  });

  it("refuses a redirect that writes a file", () => {
    // The head reads; the command as a whole writes.
    expect(bashClass("cat a > b")).toBeNull();
    expect(bashClass("echo hi >> log.txt")).toBeNull();
  });

  it("refuses sed in place, and allows it only when printing", () => {
    expect(bashClass("sed -i 's/a/b/' f")).toBeNull();
    expect(bashClass("sed 's/a/b/' f")).toBeNull();
    expect(bashClass("sed -n '1,5p' f")).toBe("read");
  });

  it("refuses command substitution and backticks", () => {
    expect(bashClass("cat $(find . -name x)")).toBeNull();
    expect(bashClass("echo `whoami`")).toBeNull();
  });

  it("refuses arbitrary interpreters even behind a read-looking prefix", () => {
    expect(bashClass('cd /tmp && python3 -c "print(1)"')).toBeNull();
    expect(bashClass("node -e 'x'")).toBeNull();
    expect(bashClass("bash script.sh")).toBeNull();
  });

  it("refuses control flow and heredocs", () => {
    expect(bashClass("for f in *; do cat $f; done")).toBeNull();
    expect(bashClass("cat <<'EOF'\nhi\nEOF")).toBeNull();
  });

  it("refuses when ANY stage of a pipeline is not read-only", () => {
    // One mutating stage disqualifies the whole command. This is the rule that
    // keeps a hidden write out of a collapsed count.
    expect(bashClass("cat f | tee out.txt")).toBeNull();
    expect(bashClass("ls && rm -rf build")).toBeNull();
    expect(bashClass("grep -n x f && git commit -am wip")).toBeNull();
  });

  it("refuses an empty or unknown command rather than guessing", () => {
    expect(bashClass("")).toBeNull();
    expect(bashClass(undefined)).toBeNull();
    expect(bashClass("some-unknown-binary --go")).toBeNull();
  });
});
