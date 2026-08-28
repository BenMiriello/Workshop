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
CHECK = REPO / "scripts" / "check_no_hardcoding.sh"
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
    (planted / "index.js").write_text('const p = "/home/someuser/.cache/x";\n')
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
    prog = f"EXCLUDES=()\n{fn}\n_grep_in {str(directory)!r} -nE '/home/[a-z]'\n"
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
    (planted / "index.js").write_text('const p = "/home/someuser/.cache/x";\n')
    assert _run_probe(origin) == _run_probe(wt) == []


def test_a_tracked_violation_is_still_caught_in_a_worktree(repo_with_worktree):
    """The fix must not make the gate blind. Narrowing to tracked files is only
    correct if a tracked violation still fails."""
    origin, wt = repo_with_worktree
    (wt / "tracked.txt").write_text('home = "/home/someuser/thing"\n')
    _git("add", "-A", cwd=wt)
    hits = _run_probe(wt)
    assert any("tracked.txt" in h for h in hits), (
        f"a tracked violation went unseen in a worktree: {hits}")


def test_a_directory_with_no_git_at_all_still_takes_the_no_index_path(tmp_path):
    """The base extract has no `.git` of any kind and must keep being scanned
    with --no-index, or delta mode compares a scan against nothing."""
    plain = tmp_path / "extract"
    (plain / "sub").mkdir(parents=True)
    (plain / "sub" / "f.txt").write_text('p = "/home/someuser/x"\n')
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
