"""The retention pass: trash, working copies and throwaway expiry, on throwaway
repos under tmp_path only."""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from services.storage import checkouts, expire, measure, trash

OLD = time.time() - 40 * 86400


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / "rt"))
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(tmp_path / "rt" / "projects.json"))
    monkeypatch.delenv("WORKSHOP_SCOPED_EXEC_BINDS", raising=False)
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@example.invalid")


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def app_repo(tmp_path):
    repo = tmp_path / "projects" / "app"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "a")
    return repo


def copy(repo, name, *, branch=None, commit=False, old=False):
    tree = repo.parent / name
    git(repo, "worktree", "add", "-q", *(["-b", branch] if branch else ["--detach"]), str(tree))
    if commit:
        (tree / "b.txt").write_text("b")
        git(tree, "add", ".")
        env = {**os.environ, "GIT_COMMITTER_DATE": f"@{int(OLD)}", "GIT_AUTHOR_DATE": f"@{int(OLD)}"}
        git(tree, "commit", "-qm", "b", env=env)
    return tree


def wt_of(repo, tree):
    from services import git_service
    return next(w for w in git_service.worktree_list(repo) if os.path.samefile(w["dir"], tree))


def test_trash_round_trip_and_expiry(tmp_path):
    victim = tmp_path / "junk"
    victim.mkdir()
    (victim / "f").write_bytes(b"x" * 8192)
    entry = trash.put(victim, app="app", kind="throwaway", reason="test")
    assert not victim.exists() and entry["bytes"] >= 8192
    trash.restore(entry["id"])
    assert (victim / "f").exists() and trash.entries() == []
    entry = trash.put(victim, app="app", kind="throwaway", reason="test")
    assert trash.empty(30)["removed"] == []
    assert trash.empty(30, now=time.time() + 31 * 86400)["removed"] == [entry["id"]]
    assert trash.entries() == []


def test_a_short_disk_takes_back_the_trash_oldest_first_inside_the_window(tmp_path):
    ids = []
    for name in ("first", "second"):
        victim = tmp_path / name
        victim.mkdir()
        (victim / "f").write_bytes(b"x" * 8192)
        ids.append(trash.put(victim, app="app", kind="throwaway", reason="test")["id"])
        time.sleep(0.01)
    assert trash.empty(30, keep_free=lambda total: 0)["removed"] == []
    gone = trash.empty(30, keep_free=lambda total: total + 1)
    assert gone["removed"] == ids and gone["for_room"] == ids
    assert trash.entries() == []


def test_a_short_disk_expires_throwaway_after_a_day(tmp_path, monkeypatch):
    root = tmp_path / "projects" / "app"
    (root / "tmp" / "probe").mkdir(parents=True)
    git(root, "init", "-q")
    (root / ".gitignore").write_text("tmp/\n")
    two_days = time.time() - 2 * 86400
    os.utime(root / "tmp" / "probe", (two_days, two_days))
    monkeypatch.setattr(measure, "apps", lambda: ({"app": root}, []))
    doc = {"retention_days": 14, "apps": {}}
    assert expire.plan(doc, now=time.time())["throwaway"] == []
    found = expire.plan(doc, now=time.time(), pressed=True)["throwaway"]
    assert [os.path.basename(e["path"]) for e in found] == ["probe"]


def test_merged_clean_copy_is_removed_with_its_branch(tmp_path):
    repo = app_repo(tmp_path)
    tree = copy(repo, "merged", branch="feature")
    d = checkouts.judge(repo, "app", wt_of(repo, tree), retention_days=14, busy=set(), now=time.time())
    assert d["action"] == checkouts.REMOVE
    done = checkouts.act(repo, d)
    assert done["done"] and not tree.exists()
    assert "feature" not in git(repo, "branch")
    assert not (repo / ".git" / "worktrees" / "merged").exists()


def test_idle_unmerged_dirty_copy_is_parked_with_its_work_saved(tmp_path):
    repo = app_repo(tmp_path)
    tree = copy(repo, "idle", branch="work", commit=True)
    (tree / "new.txt").write_text("unsaved")
    os.utime(tree / "new.txt", (OLD, OLD))
    d = checkouts.judge(repo, "app", wt_of(repo, tree), retention_days=14, busy=set(), now=time.time())
    assert d["action"] == checkouts.PARK
    done = checkouts.act(repo, d)
    assert done["done"] and not tree.exists()
    assert git(repo, "show", f"{done['saved_ref']}:new.txt") == "unsaved"
    assert "work" in git(repo, "branch")


def test_recent_locked_busy_and_service_owned_copies_are_kept(tmp_path):
    repo = app_repo(tmp_path)
    recent = copy(repo, "recent", branch="r")
    (recent / "c.txt").write_text("c")
    locked = copy(repo, "locked", branch="l")
    git(repo, "worktree", "lock", str(locked))
    owned = copy(repo, "owned", branch="wt/app/x")
    now = time.time()
    for tree, busy in ((recent, set()), (locked, set()), (owned, set()), (locked, {locked / "f"})):
        d = checkouts.judge(repo, "app", wt_of(repo, tree), retention_days=14, busy=busy, now=now)
        assert d["action"] == checkouts.KEEP, d


def test_a_copy_made_in_a_sandbox_is_read_and_removed_through_its_host_path(tmp_path):
    repo = app_repo(tmp_path)
    tree = copy(repo, "sandboxed", branch="s")
    sandbox_admin = f"{measure.MOUNTPOINT}/projects/app/.git/worktrees/sandboxed"
    (tree / ".git").write_text(f"gitdir: {sandbox_admin}\n")
    d = checkouts.judge(repo, "app", wt_of(repo, tree), retention_days=14, busy=set(), now=time.time())
    assert (tree / ".git").read_text().strip() == f"gitdir: {sandbox_admin}"
    assert d["action"] == checkouts.REMOVE
    assert checkouts.act(repo, d)["done"] and not tree.exists()


def test_throwaway_expires_old_entries_but_not_kept_or_recent_ones(tmp_path):
    root = tmp_path / "projects" / "app"
    root.mkdir(parents=True)
    git(root, "init", "-q")
    (root / ".gitignore").write_text("tmp/\n")
    scratch = root / "tmp"
    for name in ("old", "keepme", "new"):
        (scratch / name).mkdir(parents=True)
        (scratch / name / "f").write_text(name)
    (scratch / ".keep").write_text("keepme\n")
    for name in ("old", "keepme"):
        for p in (scratch / name / "f", scratch / name):
            os.utime(p, (OLD, OLD))
    found = expire.throwaway("app", root, days=14, busy=set(), now=time.time())
    assert [os.path.basename(e["path"]) for e in found] == ["old"]
    assert expire.throwaway("app", root, days=14, busy={scratch / "old" / "f"}, now=time.time()) == []


def test_a_merged_copy_left_with_a_stray_file_is_removed_after_a_day(tmp_path):
    repo = app_repo(tmp_path)
    days_ago = time.time() - 3 * 86400
    env = {**os.environ, "GIT_COMMITTER_DATE": f"@{int(days_ago)}", "GIT_AUTHOR_DATE": f"@{int(days_ago)}"}
    git(repo, "commit", "--amend", "--no-edit", "-q", env=env)
    tree = copy(repo, "landed", branch="landed")
    (tree / "probe.txt").write_text("stray")
    os.utime(tree / "probe.txt", (days_ago, days_ago))
    d = checkouts.judge(repo, "app", wt_of(repo, tree), retention_days=14, busy=set(), now=time.time())
    assert d["action"] == checkouts.REMOVE
    done = checkouts.act(repo, d)
    assert done["done"] and not tree.exists() and "landed" not in git(repo, "branch")
    assert git(repo, "show", f"{done['saved_ref']}:probe.txt") == "stray"


def test_the_trash_finds_a_chat_trash_at_and_above_an_app_tree(tmp_path):
    repo = app_repo(tmp_path)
    (tmp_path / "rt").mkdir(exist_ok=True)
    (tmp_path / "rt" / "projects.json").write_text(json.dumps({"projects": {"app": {"dir": str(repo)}}}))
    chat_trash = tmp_path / f".workshop-trash-{os.getuid()}"
    chat_trash.mkdir()
    assert chat_trash in trash.roots()
