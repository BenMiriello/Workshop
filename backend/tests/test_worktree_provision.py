"""Tests for provision_worktree and its hardlink-clone primitives.

Key claims from branches-worktrees-and-environments-2026-08-20.md §13.2:

* Two hardlink-clones of the same dependency tree share inodes with the
  source — no block duplication per additional worktree.
* The preflight check refuses when free disk is below MIN_FREE_BYTES.

The inode-sharing test is a direct proxy for the block-sharing measurement:
if every file in clone1 and clone2 has the same st_ino as the corresponding
file in the source, then no content blocks were allocated — only directory
entries.  That is the "roughly source size in new blocks rather than dep
size" property the ticket requires.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.worktrees import (  # noqa: E402
    MIN_FREE_BYTES,
    _DEP_SPECS,
    _free_bytes,
    _hardlink_clone,
    _lockfile_hash,
    _write_dep_contract,
    provision_worktree,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", str(path)], check=True,
                   capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True, env=env,
    )


def _make_fake_dep_dir(parent: Path, name: str, n_files: int = 5) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    for i in range(n_files):
        (d / f"mod{i}.js").write_text("x" * 4096)
    return d


# ---------------------------------------------------------------------------
# _hardlink_clone: inode sharing (the block-sharing property)
# ---------------------------------------------------------------------------

def test_hardlink_clone_shares_inodes_with_source(tmp_path):
    """Both clones have the same inodes as the source — zero content blocks added."""
    src = _make_fake_dep_dir(tmp_path, "base_node_modules")

    clone1 = tmp_path / "wt1" / "node_modules"
    clone2 = tmp_path / "wt2" / "node_modules"
    clone1.parent.mkdir()
    clone2.parent.mkdir()

    _hardlink_clone(src, clone1)
    _hardlink_clone(src, clone2)

    src_inodes = {os.stat(f).st_ino for f in src.rglob("*") if f.is_file()}
    clone1_inodes = {os.stat(f).st_ino for f in clone1.rglob("*") if f.is_file()}
    clone2_inodes = {os.stat(f).st_ino for f in clone2.rglob("*") if f.is_file()}

    assert clone1_inodes == src_inodes, "clone1 files do not share inodes with source"
    assert clone2_inodes == src_inodes, "clone2 files do not share inodes with source"


def test_hardlink_clone_overwrites_existing_dst(tmp_path):
    """If dst exists _hardlink_clone removes it first and re-clones cleanly."""
    src = _make_fake_dep_dir(tmp_path, "src")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale.js").write_text("stale")

    _hardlink_clone(src, dst)

    assert not (dst / "stale.js").exists()
    assert (dst / "mod0.js").exists()


# ---------------------------------------------------------------------------
# _lockfile_hash
# ---------------------------------------------------------------------------

def test_lockfile_hash_absent_returns_none(tmp_path):
    assert _lockfile_hash(tmp_path / "nonexistent.json") is None


def test_lockfile_hash_stable_and_16_chars(tmp_path):
    lf = tmp_path / "package-lock.json"
    lf.write_text('{"lockfileVersion":2}')
    h1 = _lockfile_hash(lf)
    h2 = _lockfile_hash(lf)
    assert h1 == h2
    assert len(h1) == 16
    assert h1.isalnum()


# ---------------------------------------------------------------------------
# _write_dep_contract
# ---------------------------------------------------------------------------

def test_dep_contract_written_beside_dep_dir(tmp_path):
    dep = tmp_path / "node_modules"
    dep.mkdir()
    _write_dep_contract(dep, lockfile_hash="abc123", cloned_from="/src/node_modules")

    contract_path = tmp_path / "node_modules.dep-contract.json"
    assert contract_path.exists()
    data = json.loads(contract_path.read_text())
    assert data["readonly"] is True
    assert data["lockfile_hash"] == "abc123"
    assert data["cloned_from"] == "/src/node_modules"


def test_dep_contract_none_hash_is_valid(tmp_path):
    dep = tmp_path / ".venv"
    dep.mkdir()
    _write_dep_contract(dep, lockfile_hash=None, cloned_from="/src/.venv")
    data = json.loads((tmp_path / ".venv.dep-contract.json").read_text())
    assert data["lockfile_hash"] is None


# ---------------------------------------------------------------------------
# _free_bytes
# ---------------------------------------------------------------------------

def test_free_bytes_returns_positive(tmp_path):
    assert _free_bytes(tmp_path) > 0


# ---------------------------------------------------------------------------
# provision_worktree: preflight disk check
# ---------------------------------------------------------------------------

def test_provision_preflight_refuses_when_disk_low(monkeypatch, tmp_path):
    """provision_worktree raises ValueError before touching git when disk is low."""
    import services.worktrees as wt_mod

    # Make _project_dir return a real path and _free_bytes report below floor.
    monkeypatch.setattr(wt_mod, "_project_dir", lambda _slug: tmp_path)
    monkeypatch.setattr(wt_mod, "_free_bytes", lambda _path: MIN_FREE_BYTES - 1)

    with pytest.raises(ValueError, match="preflight failed"):
        provision_worktree("project:test-proj", "owner-1")


# ---------------------------------------------------------------------------
# provision_worktree: end-to-end with a real git repo
# ---------------------------------------------------------------------------

@pytest.fixture()
def project_with_deps(tmp_path, monkeypatch):
    """A minimal git project with fake dep dirs and a projects registry."""
    import services.worktrees as wt_mod

    proj_dir = tmp_path / "projects" / "myproj"
    proj_dir.mkdir(parents=True)
    _init_git_repo(proj_dir)

    # Fake node_modules + lockfile
    nm = proj_dir / "node_modules"
    nm.mkdir()
    (nm / "lib.js").write_text("fake")
    (proj_dir / "package-lock.json").write_text('{"lockfileVersion":2}')

    # Point BRANCHES_ROOT somewhere writable
    branches = tmp_path / "branches"
    branches.mkdir()
    monkeypatch.setattr(wt_mod, "BRANCHES_ROOT", branches)

    # Fake projects registry
    registry = tmp_path / "projects.json"
    registry.write_text(
        json.dumps({"projects": {"myproj": {"dir": str(proj_dir)}}})
    )
    monkeypatch.setattr(wt_mod, "PROJECTS_REGISTRY", registry)

    # Ensure free-disk check passes
    monkeypatch.setattr(wt_mod, "_free_bytes", lambda _: MIN_FREE_BYTES * 10)

    return proj_dir


def test_provision_clones_dep_dirs_into_worktree(project_with_deps):
    result = provision_worktree("project:myproj", "task-abc")
    wt_dir = Path(result["dir"])

    assert "node_modules" in result["provisioned"]
    assert (wt_dir / "node_modules" / "lib.js").exists()


def test_provision_dep_shares_inodes_with_canonical(project_with_deps):
    """The provisioned node_modules shares inodes with the canonical tree."""
    result = provision_worktree("project:myproj", "task-inode-check")
    wt_dir = Path(result["dir"])

    canonical_inode = os.stat(project_with_deps / "node_modules" / "lib.js").st_ino
    worktree_inode = os.stat(wt_dir / "node_modules" / "lib.js").st_ino
    assert worktree_inode == canonical_inode


def test_provision_writes_dep_contract(project_with_deps):
    result = provision_worktree("project:myproj", "task-contract")
    wt_dir = Path(result["dir"])

    contract = json.loads((wt_dir / "node_modules.dep-contract.json").read_text())
    assert contract["readonly"] is True
    assert contract["cloned_from"].endswith("node_modules")


def test_provision_skips_absent_dep_dirs(project_with_deps):
    """Dep dirs not present in the canonical tree appear in skipped, not provisioned."""
    result = provision_worktree("project:myproj", "task-skip")
    # backend/.venv and .venv don't exist in the fake project
    assert "backend/.venv" in result["skipped"]
    assert ".venv" in result["skipped"]


def test_two_provisions_share_inodes(project_with_deps):
    """Provisioning twice: both worktrees share the same inodes (no block duplication)."""
    r1 = provision_worktree("project:myproj", "task-first")
    r2 = provision_worktree("project:myproj", "task-second")

    canonical = project_with_deps / "node_modules"
    base_inodes = {os.stat(f).st_ino for f in canonical.rglob("*") if f.is_file()}

    wt1_inodes = {
        os.stat(f).st_ino
        for f in (Path(r1["dir"]) / "node_modules").rglob("*")
        if f.is_file()
    }
    wt2_inodes = {
        os.stat(f).st_ino
        for f in (Path(r2["dir"]) / "node_modules").rglob("*")
        if f.is_file()
    }

    assert wt1_inodes == base_inodes, "worktree1 files do not share inodes with canonical"
    assert wt2_inodes == base_inodes, "worktree2 files do not share inodes with canonical"
