"""The completion gate must give the same verdict in a linked worktree.

A LINKED WORKTREE'S `.git` IS A FILE, not a directory: a one-line `gitdir:`
pointer back into the canonical repo. `scripts/check_no_hardcoding.sh` decided
whether a directory was a repo with `[[ -d "$dir/.git" ]]`, so it answered "not
a repo" for exactly the trees this platform runs isolated work in, and fell
through to `git grep --no-index --no-exclude-standard`, which scans the whole
directory rather than the tracked files.

That fall-through is not a harmless second opinion. It walked into
`node_modules/` and `frontend/dist/`, both gitignored, and reported a clean
branch as 12 new absolute home paths and 809 new email addresses, none of them
in the change. The same change reviewed in the canonical tree reported none, so
the gate was unusable for per-run worktrees, which is the isolation model the
work loop is built on.

These run the real shell function extracted from the script rather than a Python
restatement, so a drift between the test and the script shows up here. Same
approach as test_sweep_billing_scope.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# A NEUTRAL MARKER, not a literal home path. What these tests exercise is
# whether the probe reaches a gitignored file at all, and that works with any
# distinctive string. Writing a real `/home/<name>/...` here, copied out of the
# incident it reproduces, made this file a genuine hit in the real
# check_no_hardcoding run: the gate working correctly on its own test. The
# alternative was adding this file to the checker's exclude list, which would
# stop it being scanned for a hardcoded path someone adds here later.
MARKER = "ZZPROBE-ONLY-MARKER"
CHECK = REPO / "scripts" / "check_no_hardcoding.sh"
DESIGN = REPO / "scripts" / "check_design.sh"
AUTO_PR = REPO / "scripts" / "auto_pr.sh"


def _extract(script: Path, name: str) -> str:
    src = script.read_text()
    start = src.index(f"{name}() {{")
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(("git",) + args, cwd=cwd, capture_output=True,
                          text=True, timeout=120)


@pytest.fixture
def repo_with_worktree(tmp_path: Path):
    """A real repo with a real linked worktree, and an ignored directory in it
    holding the kind of string the gate looks for."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@example.invalid", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / ".gitignore").write_text("node_modules/\n")
    (origin / "tracked.txt").write_text("nothing interesting here\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "init", cwd=origin)

    wt = tmp_path / "linked"
    r = _git("worktree", "add", "-q", "--detach", str(wt), "HEAD", cwd=origin)
    assert r.returncode == 0, r.stderr

    planted = wt / "node_modules" / "pkg"
    planted.mkdir(parents=True)
    (planted / "index.js").write_text(f'const p = "{MARKER}";\n')
    return origin, wt


def test_a_linked_worktree_dot_git_is_a_file(repo_with_worktree):
    """The premise. If this ever stops holding, the rest of the file is moot."""
    _, wt = repo_with_worktree
    assert (wt / ".git").exists()
    assert (wt / ".git").is_file()
    assert not (wt / ".git").is_dir()


def _run_probe(directory: Path) -> list[str]:
    """Execute check_no_hardcoding.sh's own _grep_in against `directory`."""
    fn = _extract(CHECK, "_grep_in")
    prog = f"EXCLUDES=()\n{fn}\n_grep_in {str(directory)!r} -nF {MARKER!r}\n"
    out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True,
                         timeout=120)
    return [l for l in out.stdout.splitlines() if l.strip()]


def test_the_probe_stays_inside_tracked_files_in_a_worktree(repo_with_worktree):
    """The regression. An ignored directory is not part of the change and must
    not be scanned, in a worktree exactly as in the canonical tree."""
    _, wt = repo_with_worktree
    hits = _run_probe(wt)
    assert not any("node_modules" in h for h in hits), (
        f"the probe reached a gitignored directory in a worktree: {hits}")


def test_the_worktree_and_the_canonical_tree_agree(repo_with_worktree):
    """The ticket's actual acceptance: the same content gives the same verdict
    whichever tree it is reviewed in."""
    origin, wt = repo_with_worktree
    planted = origin / "node_modules" / "pkg"
    planted.mkdir(parents=True)
    (planted / "index.js").write_text(f'const p = "{MARKER}";\n')
    assert _run_probe(origin) == _run_probe(wt) == []


def test_a_tracked_violation_is_still_caught_in_a_worktree(repo_with_worktree):
    """The fix must not make the gate blind. Narrowing to tracked files is only
    correct if a tracked violation still fails."""
    origin, wt = repo_with_worktree
    (wt / "tracked.txt").write_text(f'x = "{MARKER}"\n')
    _git("add", "-A", cwd=wt)
    hits = _run_probe(wt)
    assert any("tracked.txt" in h for h in hits), (
        f"a tracked violation went unseen in a worktree: {hits}")


def test_a_directory_with_no_git_at_all_still_takes_the_no_index_path(tmp_path):
    """The base extract has no `.git` of any kind and must keep being scanned
    with --no-index, or delta mode compares a scan against nothing."""
    plain = tmp_path / "extract"
    (plain / "sub").mkdir(parents=True)
    (plain / "sub" / "f.txt").write_text(f'p = "{MARKER}"\n')
    assert not (plain / ".git").exists()
    assert any("f.txt" in h for h in _run_probe(plain))


@pytest.mark.parametrize("script", [CHECK, AUTO_PR])
def test_no_script_tests_dot_git_as_a_directory(script):
    """The assumption itself, wherever it lives. Both scripts made it and both
    were fixed in one change; a third would fail the same way."""
    code = "\n".join(l for l in script.read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    assert '-d "$dir/.git"' not in code
    assert '-d "$REPO/.git"' not in code


@pytest.mark.parametrize("script", [CHECK, AUTO_PR])
def test_the_scripts_parse(script):
    assert subprocess.run(["bash", "-n", str(script)],
                          capture_output=True).returncode == 0


class TestTheDesignCheckDoesNotFailEveryWorktree:
    """The other half of the same acceptance, found by review of the first half.

    check_design.sh asks whether a change's committed UI work is in the built
    bundle. In a linked worktree that question has no honest answer: there is no
    `frontend/dist` (gitignored, and provisioning hardlinks node_modules and the
    venv, not dist), and the built bundle belongs to the canonical tree, which
    does not have this branch until it merges. It read a missing build stamp as
    "we cannot show this is published, so we must not claim it is" and failed.

    Measured before the fix: exit 1 with "(no usable build stamp on
    frontend/dist)". So reviewer.sh still gave a different verdict in a worktree
    than in the canonical tree for ANY change touching frontend/ or
    packages/ui/, which is most of what the loop works.
    """

    def test_it_detects_a_linked_worktree_the_same_way_apply_does(self):
        """One rule, not two. apply.sh compares git-dir with git-common-dir; a
        second definition of 'am I in a worktree' is how two checks start
        disagreeing about it."""
        code = "\n".join(l for l in DESIGN.read_text().splitlines()
                         if not l.lstrip().startswith("#"))
        assert "--git-common-dir" in code
        assert "IN_LINKED_WORKTREE" in code

    def test_the_bundle_branch_is_skipped_in_a_worktree_not_failed(self):
        code = DESIGN.read_text()
        i = code.index("IN_LINKED_WORKTREE=1")
        branch = code[i:i + 1200]
        assert "warn:" in branch, "a worktree still takes a failing branch"

    def test_the_canonical_tree_still_answers_the_question(self):
        """The fix must not delete the check. On the canonical tree the bundle
        question is answerable and must still be asked, or a UI change ships
        unbuilt, which is the failure the check exists for."""
        code = DESIGN.read_text()
        assert "not in the built bundle" in code
        assert "design_status=1" in code

    def test_it_parses(self):
        assert subprocess.run(["bash", "-n", str(DESIGN)],
                              capture_output=True).returncode == 0
