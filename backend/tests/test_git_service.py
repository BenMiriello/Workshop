"""`services/git_service.py` against real git repositories, no mocks.

The service is the one place in the backend that speaks git (section 13.1 of
workspace-ownership-and-leases-2026-08-22.md), so a mocked `subprocess` would
test nothing that matters: every claim here is about what git actually does with
the argv the service builds. Each test therefore runs against a real repository
built in `tmp_path`, and the network is never touched (the one push test pushes
into a local bare repo on the same disk).

What is pinned, and why:

* The verbs a caller depends on for CONTROL FLOW return structure, not text:
  `merge` says whether it fast-forwarded and which paths conflicted, `snapshot`
  says whether the tree was clean.
* A failed merge leaves the working tree CLEAN. That is the property that makes
  the merge verb safe to call from a route; a repo left mid-merge is the scar
  the workspace-ownership design exists to prevent.
* `snapshot` produces a real, diffable commit that is NOT on the branch and does
  NOT move HEAD, which is section 9's whole requirement for yielding a
  workspace.
* Commits land under the neutral Workshop Projects identity regardless of the
  ambient git config, because history has to read consistently no matter which
  operator or agent triggered the write.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import git_service  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)],
                   check=True, capture_output=True)
    return path


def _write(repo: Path, rel: str, text: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _stage(repo: Path, *paths: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", *(paths or ["-A"])],
                   check=True, capture_output=True)


def _commit(repo: Path, rel: str, text: str, message: str) -> str:
    _write(repo, rel, text)
    _stage(repo)
    r = git_service.commit(repo, message)
    assert r["ok"], r["detail"]
    return r["commit"]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository with one commit on `main`."""
    r = _init(tmp_path / "repo")
    _commit(r, "README.md", "one\n", "initial")
    return r


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

class TestInspection:
    def test_is_repo_distinguishes_a_repo_from_a_plain_directory(self, repo, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert git_service.is_repo(repo) is True
        assert git_service.is_repo(plain) is False

    def test_rev_parse_resolves_head_and_returns_none_for_a_missing_ref(self, repo):
        head = git_service.rev_parse(repo, "HEAD")
        assert head and len(head) == 40
        assert git_service.rev_parse(repo, "nope", verify=True) is None

    def test_rev_parse_abbrev_ref_gives_the_branch_name(self, repo):
        assert git_service.rev_parse(repo, "HEAD", abbrev_ref=True) == "main"

    def test_main_branch_prefers_main_then_master_then_head(self, tmp_path):
        m = _init(tmp_path / "m")
        _commit(m, "a.txt", "a\n", "init")
        assert git_service.main_branch(m) == "main"

        legacy = _init(tmp_path / "legacy")
        _commit(legacy, "a.txt", "a\n", "init")
        subprocess.run(["git", "-C", str(legacy), "branch", "-m", "main", "master"],
                       check=True, capture_output=True)
        assert git_service.main_branch(legacy) == "master"

        other = _init(tmp_path / "other")
        _commit(other, "a.txt", "a\n", "init")
        subprocess.run(["git", "-C", str(other), "branch", "-m", "main", "trunk"],
                       check=True, capture_output=True)
        assert git_service.main_branch(other) == "trunk"

    def test_status_reports_clean_then_the_dirty_entries(self, repo):
        st = git_service.status(repo)
        assert st == {"branch": "main", "clean": True, "entries": []}

        _write(repo, "README.md", "two\n")
        _write(repo, "new.txt", "new\n")
        st = git_service.status(repo)
        assert st["branch"] == "main"
        assert st["clean"] is False
        by_path = {e["path"]: e["status"] for e in st["entries"]}
        assert by_path["README.md"].strip() == "M"
        assert by_path["new.txt"] == "??"

    def test_log_is_newest_first_and_carries_subject_and_author(self, repo):
        _commit(repo, "b.txt", "b\n", "second")
        entries = git_service.log(repo)
        assert [e["subject"] for e in entries] == ["second", "initial"]
        assert entries[0]["author"] == git_service.IDENTITY_NAME
        assert entries[0]["date"]
        assert len(entries[0]["sha"]) == 40

    def test_log_respects_max_count_and_survives_an_empty_repo(self, repo, tmp_path):
        _commit(repo, "b.txt", "b\n", "second")
        assert len(git_service.log(repo, max_count=1)) == 1
        empty = _init(tmp_path / "empty")
        assert git_service.log(empty) == []

    def test_tag_shas_returns_what_tags_point_at(self, repo):
        assert git_service.tag_shas(repo) == set()
        head = git_service.rev_parse(repo, "HEAD")
        subprocess.run(["git", "-C", str(repo), "tag", "v1"],
                       check=True, capture_output=True)
        assert git_service.tag_shas(repo) == {head}


class TestDiff:
    @pytest.fixture()
    def diverged(self, repo):
        """`main` plus a `feature` branch that changed one file and added one."""
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        _commit(repo, "README.md", "one\ntwo\n", "extend readme")
        _commit(repo, "added.txt", "added\n", "add a file")
        subprocess.run(["git", "-C", str(repo), "checkout", "main"],
                       check=True, capture_output=True)
        return repo

    def test_stat_summarises_ref_to_ref(self, diverged):
        summary = git_service.diff_stat(diverged, "main", "feature", three_dot=True)
        assert "2 files changed" in summary

    def test_name_status_lists_the_changed_paths_with_their_letter(self, diverged):
        entries = git_service.diff_name_status(diverged, "main", "feature",
                                               three_dot=True)
        assert {e["path"]: e["status"] for e in entries} == {
            "README.md": "M", "added.txt": "A",
        }

    def test_unified_diff_can_be_limited_to_one_path(self, diverged):
        patch = git_service.diff(diverged, "main", "feature", three_dot=True,
                                 paths=["added.txt"])
        assert "added.txt" in patch
        assert "README.md" not in patch
        assert "+added" in patch

    def test_two_dot_and_three_dot_are_both_available(self, diverged):
        assert git_service.diff(diverged, "main", "feature") != ""
        assert git_service.diff(diverged, "main", "feature", three_dot=True) != ""

    def test_a_clean_diff_is_empty_rather_than_an_error(self, repo):
        assert git_service.diff_stat(repo, "HEAD", "HEAD", three_dot=True) == ""
        assert git_service.diff_name_status(repo, "HEAD", "HEAD") == []
        assert git_service.diff(repo, "HEAD", "HEAD") == ""


class TestAheadBehindAndAncestry:
    def test_ahead_behind_counts_both_sides(self, repo):
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        _commit(repo, "f1.txt", "1\n", "f1")
        _commit(repo, "f2.txt", "2\n", "f2")
        subprocess.run(["git", "-C", str(repo), "checkout", "main"],
                       check=True, capture_output=True)
        _commit(repo, "m1.txt", "m\n", "m1")

        assert git_service.ahead_behind(repo, "main", "feature") == {"ahead": 2, "behind": 1}
        assert git_service.ahead_behind(repo, "feature", "main") == {"ahead": 1, "behind": 2}

    def test_ahead_behind_of_a_missing_ref_is_zeros_not_an_exception(self, repo):
        assert git_service.ahead_behind(repo, "origin/nope") == {"ahead": 0, "behind": 0}

    def test_is_ancestor_is_true_only_for_contained_history(self, repo):
        first = git_service.rev_parse(repo, "HEAD")
        second = _commit(repo, "b.txt", "b\n", "second")
        assert git_service.is_ancestor(repo, first, second) is True
        assert git_service.is_ancestor(repo, second, first) is False
        assert git_service.is_ancestor(repo, "nope", second) is False


# ---------------------------------------------------------------------------
# Branches and worktrees
# ---------------------------------------------------------------------------

class TestBranches:
    def test_create_list_and_delete(self, repo):
        assert git_service.branch_list(repo) == ["main"]
        git_service.branch_create(repo, "topic")
        assert sorted(git_service.branch_list(repo)) == ["main", "topic"]
        assert git_service.branch_list(repo, pattern="top*") == ["topic"]
        assert git_service.branch_delete(repo, "topic") is True
        assert git_service.branch_list(repo) == ["main"]

    def test_create_at_a_start_point(self, repo):
        first = git_service.rev_parse(repo, "HEAD")
        _commit(repo, "b.txt", "b\n", "second")
        git_service.branch_create(repo, "from-first", first)
        assert git_service.rev_parse(repo, "from-first") == first

    def test_creating_a_branch_that_exists_raises_giterror(self, repo):
        git_service.branch_create(repo, "topic")
        with pytest.raises(git_service.GitError) as e:
            git_service.branch_create(repo, "topic")
        assert e.value.returncode != 0
        assert "topic" in (e.value.stderr + e.value.stdout)

    def test_deleting_a_missing_branch_is_false_not_an_exception(self, repo):
        assert git_service.branch_delete(repo, "never-existed") is False


class TestWorktrees:
    def test_add_list_lock_remove_and_prune(self, repo, tmp_path):
        target = tmp_path / "wt" / "one"
        git_service.worktree_add(repo, target, branch="wt/one")
        assert (target / "README.md").exists()

        entries = git_service.worktree_list(repo)
        assert len(entries) == 2
        record = next(e for e in entries if e["branch"] == "wt/one")
        assert Path(record["dir"]).resolve() == target.resolve()
        assert record["head"] and len(record["head"]) == 40
        assert record["locked"] is False

        assert git_service.worktree_lock(repo, target) is True
        locked = next(e for e in git_service.worktree_list(repo)
                      if e["branch"] == "wt/one")
        assert locked["locked"] is True

        subprocess.run(["git", "-C", str(repo), "worktree", "unlock", str(target)],
                       check=True, capture_output=True)
        assert git_service.worktree_remove(repo, target, force=True) is True
        assert not target.exists()
        assert [e["branch"] for e in git_service.worktree_list(repo)] == ["main"]
        assert git_service.worktree_prune(repo) is True

    def test_a_locked_worktree_refuses_a_single_force(self, repo, tmp_path):
        """git wants --force TWICE to remove a locked worktree, and this service
        passes it once. That is deliberate: a lock is how a crashed holder's
        worktree announces itself, and the caller that reaps one
        (`worktrees._reap_worktree`) treats the refusal as expected and falls
        back to deleting the directory itself. Pinned so the day someone
        "fixes" `worktree_remove` to double-force, that reap path's fallback
        does not silently become dead code."""
        target = tmp_path / "wt" / "locked"
        git_service.worktree_add(repo, target, branch="wt/locked")
        git_service.worktree_lock(repo, target)

        assert git_service.worktree_remove(repo, target, force=True) is False
        assert target.exists()

    def test_add_from_a_start_point(self, repo, tmp_path):
        first = git_service.rev_parse(repo, "HEAD")
        _commit(repo, "b.txt", "b\n", "second")
        target = tmp_path / "wt" / "from-first"
        git_service.worktree_add(repo, target, branch="wt/from-first",
                                 start_point=first)
        assert git_service.rev_parse(target, "HEAD") == first

    def test_adding_over_an_existing_path_raises_giterror(self, repo, tmp_path):
        target = tmp_path / "wt" / "one"
        git_service.worktree_add(repo, target, branch="wt/one")
        with pytest.raises(git_service.GitError) as e:
            git_service.worktree_add(repo, target, branch="wt/two")
        assert e.value.returncode != 0

    def test_removing_an_unknown_worktree_is_false_not_an_exception(self, repo, tmp_path):
        assert git_service.worktree_remove(repo, tmp_path / "nothing-here") is False

    def test_prune_drops_metadata_for_a_deleted_directory(self, repo, tmp_path):
        import shutil

        target = tmp_path / "wt" / "gone"
        git_service.worktree_add(repo, target, branch="wt/gone")
        shutil.rmtree(target)
        assert any(e["branch"] == "wt/gone" for e in git_service.worktree_list(repo))
        git_service.worktree_prune(repo)
        assert [e["branch"] for e in git_service.worktree_list(repo)] == ["main"]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

class TestCommit:
    def test_commit_returns_the_new_sha(self, repo):
        before = git_service.rev_parse(repo, "HEAD")
        sha = _commit(repo, "b.txt", "b\n", "second")
        assert sha != before
        assert git_service.rev_parse(repo, "HEAD") == sha

    def test_commit_with_nothing_staged_is_a_structured_refusal(self, repo):
        r = git_service.commit(repo, "empty")
        assert r["ok"] is False
        assert r["commit"] is None
        assert r["detail"]

    def test_allow_empty_makes_that_commit(self, repo):
        r = git_service.commit(repo, "empty on purpose", allow_empty=True)
        assert r["ok"] is True
        assert git_service.log(repo)[0]["subject"] == "empty on purpose"

    def test_all_picks_up_a_tracked_modification_without_staging(self, repo):
        _write(repo, "README.md", "changed\n")
        r = git_service.commit(repo, "tracked change", all=True)
        assert r["ok"] is True
        assert git_service.status(repo)["clean"] is True

    def test_the_committer_identity_is_the_neutral_one(self, repo):
        # Set a different ambient identity: the service must override it so
        # history reads consistently whoever triggered the write.
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Somebody Else"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "else@example.com"],
                       check=True, capture_output=True)
        _commit(repo, "b.txt", "b\n", "second")
        r = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%an%x1f%ae%x1f%cn%x1f%ce"],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip().split("\x1f") == [
            git_service.IDENTITY_NAME, git_service.IDENTITY_EMAIL,
            git_service.IDENTITY_NAME, git_service.IDENTITY_EMAIL,
        ]


class TestMerge:
    def _feature(self, repo: Path, path: str, text: str) -> str:
        subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"],
                       check=True, capture_output=True)
        sha = _commit(repo, path, text, f"feature touches {path}")
        subprocess.run(["git", "-C", str(repo), "checkout", "main"],
                       check=True, capture_output=True)
        return sha

    def test_a_mergeable_branch_fast_forwards(self, repo):
        tip = self._feature(repo, "f.txt", "f\n")
        result = git_service.merge(repo, "feature")
        assert result["ok"] is True
        assert result["fast_forward"] is True
        assert result["conflict"] is False
        assert result["commit"] == tip

    def test_divergence_produces_a_merge_commit_with_the_given_message(self, repo):
        self._feature(repo, "f.txt", "f\n")
        _commit(repo, "m.txt", "m\n", "main moves on")
        result = git_service.merge(repo, "feature", message="Merge feature into main")
        assert result["ok"] is True
        assert result["fast_forward"] is False
        assert git_service.log(repo)[0]["subject"] == "Merge feature into main"
        assert (repo / "f.txt").exists()

    def test_ff_only_refuses_divergence_instead_of_making_a_commit(self, repo):
        self._feature(repo, "f.txt", "f\n")
        before = _commit(repo, "m.txt", "m\n", "main moves on")
        result = git_service.merge(repo, "feature", allow_merge_commit=False)
        assert result["ok"] is False
        assert result["fast_forward"] is False
        assert result["detail"]
        assert git_service.rev_parse(repo, "HEAD") == before

    def test_a_conflict_names_the_paths_and_leaves_the_tree_clean(self, repo):
        self._feature(repo, "README.md", "from the feature\n")
        before = _commit(repo, "README.md", "from main\n", "main edits the same file")

        result = git_service.merge(repo, "feature", message="Merge feature into main")

        assert result["ok"] is False
        assert result["conflict"] is True
        assert result["conflicts"] == ["README.md"]
        assert result["commit"] is None
        assert "CONFLICT" in result["detail"] or "conflict" in result["detail"].lower()
        # The abort is the point: HEAD did not move and nothing is mid-merge.
        assert git_service.rev_parse(repo, "HEAD") == before
        assert git_service.status(repo)["clean"] is True
        assert not (repo / ".git" / "MERGE_HEAD").exists()
        assert (repo / "README.md").read_text() == "from main\n"

    def test_merging_a_missing_ref_is_a_structured_failure(self, repo):
        result = git_service.merge(repo, "no-such-branch")
        assert result["ok"] is False
        assert result["detail"]


class TestSnapshot:
    def test_a_dirty_tree_snapshots_to_a_dangling_commit(self, repo):
        head = git_service.rev_parse(repo, "HEAD")
        _write(repo, "README.md", "work in progress\n")

        snap = git_service.snapshot(repo)

        assert snap["ok"] is True
        assert snap["clean"] is False
        assert snap["sha"] and len(snap["sha"]) == 40
        # HEAD did not move and the branch did not gain a commit.
        assert git_service.rev_parse(repo, "HEAD") == head
        assert git_service.rev_parse(repo, "main") == head
        # The snapshot is a real, diffable commit that holds the work.
        patch = git_service.diff(repo, "HEAD", snap["sha"])
        assert "work in progress" in patch
        # And the working tree still has the edit: yielding does not discard it.
        assert (repo / "README.md").read_text() == "work in progress\n"

    def test_a_clean_tree_snapshots_to_nothing_and_says_so(self, repo):
        snap = git_service.snapshot(repo)
        assert snap == {"ok": True, "sha": None, "ref": None, "clean": True, "detail": ""}

    def test_a_named_ref_makes_the_snapshot_reachable(self, repo):
        _write(repo, "README.md", "wip\n")
        snap = git_service.snapshot(repo, ref="refs/wip/session-1",
                                    message="yield: session-1")
        assert snap["ok"] is True
        assert snap["ref"] == "refs/wip/session-1"
        assert git_service.rev_parse(repo, "refs/wip/session-1") == snap["sha"]
        # git's own stash subject wraps the message ("On <branch>: <message>").
        assert "yield: session-1" in git_service.log(repo, ref="refs/wip/session-1")[0]["subject"]


class TestCheckoutAndClean:
    def test_checkout_moves_head_and_can_create(self, repo):
        assert git_service.checkout(repo, "topic", create=True)["ok"] is True
        assert git_service.rev_parse(repo, "HEAD", abbrev_ref=True) == "topic"
        assert git_service.checkout(repo, "main")["ok"] is True
        assert git_service.rev_parse(repo, "HEAD", abbrev_ref=True) == "main"

    def test_checking_out_a_missing_ref_is_a_structured_refusal(self, repo):
        r = git_service.checkout(repo, "nope")
        assert r["ok"] is False
        assert r["detail"]
        assert git_service.rev_parse(repo, "HEAD", abbrev_ref=True) == "main"

    def test_clean_removes_untracked_files_and_reports_them(self, repo):
        _write(repo, "junk.txt", "junk\n")
        _write(repo, "sub/deeper.txt", "deep\n")

        dry = git_service.clean(repo, dry_run=True)
        assert sorted(dry) == ["junk.txt", "sub/"]
        assert (repo / "junk.txt").exists()

        removed = git_service.clean(repo)
        assert sorted(removed) == ["junk.txt", "sub/"]
        assert not (repo / "junk.txt").exists()
        assert not (repo / "sub").exists()
        assert git_service.status(repo)["clean"] is True

    def test_clean_leaves_a_tracked_modification_alone(self, repo):
        _write(repo, "README.md", "edited\n")
        git_service.clean(repo)
        assert (repo / "README.md").read_text() == "edited\n"


class TestPush:
    def test_push_sends_the_branch_to_a_local_remote(self, repo, tmp_path):
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(bare)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)],
                       check=True, capture_output=True)

        r = git_service.push(repo, "origin", "main", set_upstream=True)

        assert r["ok"] is True
        assert git_service.rev_parse(bare, "main") == git_service.rev_parse(repo, "HEAD")

    def test_pushing_to_a_missing_remote_is_a_structured_failure(self, repo):
        r = git_service.push(repo, "origin", "main")
        assert r["ok"] is False
        assert r["detail"]


class TestItKnowsNothingButGit:
    def test_the_module_imports_no_project_aware_service(self):
        """Section 13.2's layering rule, mechanically. The git service is BELOW
        branches, tickets, leases and apps: if it ever imports one of them the
        boundary this ticket drew is gone, and the service stops being usable
        against an arbitrary repository."""
        source = Path(git_service.__file__).read_text()
        for forbidden in ("worktrees", "task_scope", "services.tasks",
                          "registry", "app_runner", "projects"):
            assert f"import {forbidden}" not in source
            assert f"from services import {forbidden}" not in source

    def test_every_public_verb_takes_the_repository_first(self):
        import inspect

        skip = {"git_env", "GitError"}
        for name, fn in vars(git_service).items():
            if name.startswith("_") or name in skip or not inspect.isfunction(fn):
                continue
            if fn.__module__ != git_service.__name__:
                continue
            first = list(inspect.signature(fn).parameters)[0]
            assert first == "repo", f"{name}() takes {first!r} first, not a repo"
