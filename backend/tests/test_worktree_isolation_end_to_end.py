"""Two chats working at once, in real worktrees, on a real filesystem.

THE OPERATOR'S SENTENCE, AS AN EXIT CODE. He asked for this on 2026-09-04:

    "We need to be able to work in different work trees. That hasn't been done.
    That's part of the work that will allow us to work faster and have multiple
    agents work together."

and the acceptance bar is one sentence: *a chat doing work gets its own git
worktree automatically, writes only there, and its work can be reviewed and
merged back -- and a second agent working at the same time does not see or
clobber the first one's files.* This file is that sentence, clause by clause,
against real git and real bwrap.

--------------------------------------------------------------------------
WHY THIS FILE HAD TO EXIST BEFORE THE FLAG COULD BE ARMED
--------------------------------------------------------------------------
Every piece of this was already built and every test of it was a mock. The
mount decision was tested with `leases.get` monkeypatched to a dict literal
(`test_tree_access_mount.py`); the auto-creation was tested with
`allocate_task_worktree` monkeypatched to return a string
(`test_auto_worktree_on_write.py`); the lease's own concurrency was tested
against the database and never against a directory. So the COMPOSITION -- the
only thing the operator asked for -- was the one thing nothing exercised, and
three sessions in a row read the design, agreed with it, and left the tree
unchanged. `docs/platform-redesign/worktree-as-the-place-2026-08-28.md` §9 lists
six steps; five of them were done, and nobody could tell, because there was no
command that said so.

Measured here instead:

  * `git worktree add` really runs, so the trees are linked worktrees with a
    `.git` FILE and a shared object store -- the shape that made `git status`
    fail inside a sandbox until `scoped_exec._git_worktree_binds` was written.
  * `bwrap` really runs, so "read-only" is `EROFS` from the kernel rather than
    a boolean this suite agreed with itself about.
  * `worktrees.merge_branch` really merges and really reaps, so "merged back"
    means the commit is on the base ref and the directory is gone.

The ONE substitution is the lease store, which needs Postgres and a pinned
owner. It is replaced by `_Leases` below, which implements the same
compare-and-set the real one does; `tests/test_leases.py` is what proves the
real store honours it under concurrency, and this file proves what the platform
does with the answer.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from services import (chat_context, chat_worktree, git_service, scoped_exec,
                      tree_access, worktrees)

needs_bwrap = pytest.mark.skipif(
    not scoped_exec.functional(),
    reason="bwrap cannot create a user namespace on this host")

# The scope root is bind-mounted at MOUNTPOINT, and `_git_worktree_binds` treats
# a host path under MOUNTPOINT as already-inside-the-scope. A fixture repo
# living under `/workspace` would therefore lose the bind that makes a linked
# worktree a repository, and the failure would look like a product bug. pytest's
# tmp_path is under /tmp, which is what the real installation's root looks like.
assert not str(scoped_exec.MOUNTPOINT).startswith("/tmp")


class _Leases:
    """The lease store's contract, in a dict: one holder per subject, fenced.

    `acquire` refuses a subject somebody else holds and bumps the generation
    when it grants; `fence` expires the current holder. That is exactly the
    surface `tree_access.decide` and `chat_worktree.enter` consume.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    def acquire(self, kind, subject_id, *, holder_kind, holder_id, ttl_sec=None):
        cur = self.rows.get((kind, subject_id))
        if cur and cur["state"] == "held" and str(cur["holder_id"]) != str(holder_id):
            return None
        row = {
            "id": f"lease-{len(self.rows)}", "subject_kind": kind,
            "subject_id": subject_id, "holder_kind": holder_kind,
            "holder_id": str(holder_id),
            "generation": (cur["generation"] + 1) if cur else 1,
            "state": "held", "expires_at": None,
        }
        self.rows[(kind, subject_id)] = row
        return dict(row)

    def get(self, kind, subject_id):
        row = self.rows.get((kind, subject_id))
        return dict(row) if row else None

    def fence(self, kind, subject_id):
        row = self.rows.get((kind, subject_id))
        if row:
            row["state"] = "expired"
        return True

    def renew(self, kind, subject_id, generation, *, work=False):
        row = self.rows.get((kind, subject_id))
        if not row or row["state"] != "held" or row["generation"] != generation:
            return None
        return dict(row)


class _Project(chat_context.ProjectContextProvider):
    """A real `ProjectContextProvider`, without the registry lookup.

    Subclassed rather than faked so `chat_context.is_build_provider` -- an
    `isinstance` check, and the gate `chat_worktree._project_of` opens on --
    answers for the same reason it answers in production.
    """

    def __init__(self, slug: str, directory: str) -> None:  # noqa: D107
        self.slug = slug
        self.key = f"project:{slug}"
        self.dir = directory


@pytest.fixture
def workshop(tmp_path, monkeypatch):
    """A project repo, a branches root, and the flag ON. Everything is real."""
    root = tmp_path / "root"
    proj = root / "proj"
    proj.mkdir(parents=True)
    (root / ".runtime").mkdir()

    def git(*args, cwd=proj):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True)

    git("init", "-q", "-b", "main", ".")
    git("config", "user.email", "worktree-test@example.test")
    git("config", "user.name", "worktree test")
    git("config", "commit.gpgsign", "false")
    (proj / "README.md").write_text("baseline\n")
    git("add", "README.md")
    git("commit", "-qm", "baseline")

    registry = root / ".runtime" / "projects.json"
    registry.write_text(
        '{"projects": {"probe": {"title": "Probe", "dir": "%s"}}}' % proj)

    # A HERMETIC HOME. `scoped_exec._home_binds` binds `~/.claude`,
    # `~/.claude.json` and `~/.gitconfig` into every sandbox, and binding the
    # real one makes this suite depend on the state of the machine running it --
    # measured: a `~/.claude.json` being rewritten underneath the run failed
    # every bwrap case with a mount error that looks exactly like a product bug.
    # A test owns its home.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude.json").write_text("{}\n")
    (home / ".gitconfig").write_text(
        "[user]\n\tname = worktree test\n\temail = worktree-test@example.test\n"
        "[commit]\n\tgpgsign = false\n")
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setenv("WORKSHOP_ROOT", str(root))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(root / ".runtime"))
    monkeypatch.setenv("WORKSHOP_TREE_LEASE_MOUNT", "1")
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    monkeypatch.setattr(worktrees, "BRANCHES_ROOT", root / "branches")
    monkeypatch.setattr(worktrees, "PROJECTS_REGISTRY", registry)

    # No relational store in the test environment: the record layer is
    # best-effort by design (`worktrees._record`) and `subject_id_for` falls
    # back to the realpath when no record claims the path, which is the identity
    # BOTH participants then compute. Silenced rather than left to raise so the
    # run is not a wall of swallowed tracebacks.
    from services import worktree_records
    monkeypatch.setattr(worktree_records, "list_records", lambda **kw: [])
    monkeypatch.setattr(worktree_records, "record_allocation",
                        lambda **kw: None)
    monkeypatch.setattr(worktree_records, "record_for_branch",
                        lambda *a, **kw: None)

    store = _Leases()
    from services import leases
    monkeypatch.setattr(leases, "acquire", store.acquire)
    monkeypatch.setattr(leases, "get", store.get)
    monkeypatch.setattr(leases, "fence", store.fence)
    monkeypatch.setattr(leases, "renew", store.renew)

    from services import presence
    monkeypatch.setattr(presence, "beat", lambda *a, **kw: None)
    monkeypatch.setattr(presence, "release", lambda *a, **kw: None)

    return {"root": root, "proj": proj, "leases": store,
            "provider": _Project("probe", str(proj)), "git": git}


def _commit_all(cwd: str, message: str) -> None:
    """Commit everything, new files included -- what a chat turn actually leaves."""
    assert git_service.add_all(cwd)["ok"]
    assert git_service.commit(cwd, message)["ok"]


def _thread(thread_id: str, title: str, branch_slug: str | None = None) -> dict:
    return {"id": thread_id, "context_key": "project:probe", "title": title,
            "branch_slug": branch_slug}


def _open_a_worktree(workshop, thread_id: str, title: str) -> dict:
    """What the first write does for a chat: make a tree named after the work.

    Goes through `allocate_task_worktree` exactly as
    `build_streaming._auto_create_worktree_for_chat` does, with the chat's own
    title, which is the naming rule of
    `worktree-as-the-place-2026-08-28.md` §3.
    """
    return worktrees.allocate_task_worktree(
        "probe", thread_id, title=title, allocated_by=f"chat:{thread_id}")


ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def _in_sandbox(workdir: str, session_id: str, script: str):
    """Run one shell line the way a turn runs: bwrap, scope at MOUNTPOINT."""
    argv = scoped_exec.wrap(["sh", "-c", script], workdir,
                            session_id=session_id, holder_kind="chat")
    assert argv[0].endswith("bwrap"), "the sandbox was not built; this proves nothing"
    env = dict(ENV, HOME=os.environ["HOME"])
    proc = subprocess.run(argv, cwd=workdir, timeout=120,
                          env=scoped_exec.turn_env(env, workdir),
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


# --- the name -----------------------------------------------------------------

def test_a_worktree_is_named_after_the_work_and_never_after_a_hex_id(workshop):
    """§3: "no worktree is ever created without a human-readable name"."""
    made = _open_a_worktree(workshop, "d8676bbabd924e7e8d4e6d18d48dbcdc",
                            "Chat toolbar fixes")

    assert made["branch_slug"] == "chat-toolbar-fixes"
    assert made["branch"] == "wt/probe/chat-toolbar-fixes"
    assert made["display_name"] == "Chat toolbar fixes"
    # The thread id is a 32-char hex string and appears nowhere in the name.
    assert "d8676bba" not in made["branch_slug"]


def test_two_chats_about_different_things_get_two_different_trees(workshop):
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    b = _open_a_worktree(workshop, "thread-b", "Sidebar width")

    assert a["dir"] != b["dir"]
    assert os.path.isdir(a["dir"]) and os.path.isdir(b["dir"])
    assert {a["branch_slug"], b["branch_slug"]} == {"chat-toolbar-fixes",
                                                   "sidebar-width"}


def test_a_worktree_is_cut_from_where_the_shared_tree_actually_is(workshop):
    """The defect that kept the whole mechanism off, as a test.

    The base used to be `git_service.main_branch`, a NAME heuristic that answers
    "main" whenever a ref called `main` resolves -- whatever the tree in front
    of it is doing. Measured on this installation 2026-09-04: the shared tree
    was on a feature branch 368 commits ahead of `main`, so a chat's first write
    would have handed it a checkout 368 commits stale.
    """
    workshop["git"]("checkout", "-q", "-b", "feat/long-lived")
    (workshop["proj"] / "recent.txt").write_text("two weeks of work\n")
    workshop["git"]("add", "-A")
    workshop["git"]("commit", "-qm", "work that main has never seen")

    made = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")

    assert os.path.isfile(os.path.join(made["dir"], "recent.txt")), (
        "the worktree was cut from a stale ref and is missing the shared "
        "tree's work")
    state = worktrees.branch_state("probe", made["branch_slug"])
    assert state["base_ref"] == "feat/long-lived"


def test_merging_does_not_move_the_shared_trees_checkout(workshop):
    """`merge_branch` checks the base ref out in the project's OWN tree. When
    the base was a name that did not match the checkout, a merge silently moved
    somebody else's branch out from under them."""
    workshop["git"]("checkout", "-q", "-b", "feat/long-lived")
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    open(os.path.join(a["dir"], "toolbar.txt"), "w").write("mine\n")
    _commit_all(a["dir"], "toolbar work")

    assert worktrees.merge_branch("probe", a["branch_slug"])["ok"]

    assert git_service.status(str(workshop["proj"]))["branch"] == "feat/long-lived"
    assert (workshop["proj"] / "toolbar.txt").read_text() == "mine\n"


# --- the lease ----------------------------------------------------------------

def test_one_tree_has_one_holder_and_the_second_chat_is_refused(workshop):
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    subject = tree_access.subject_id_for(a["dir"])

    first = workshop["leases"].acquire("worktree", subject,
                                       holder_kind="chat", holder_id="thread-a")
    second = workshop["leases"].acquire("worktree", subject,
                                        holder_kind="chat", holder_id="thread-b")

    assert first is not None
    assert second is None, "two chats were allowed to hold one tree"


def test_both_participants_name_the_same_tree_the_same_way(workshop):
    """Two identities for one subject is two locks on one door."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")

    from services import run_worktree
    assert (tree_access.subject_id_for(a["dir"])
            == run_worktree.plan(a["dir"], run_id="job-1").subject_id)


# --- the mount ----------------------------------------------------------------

def test_the_mount_is_read_write_only_for_the_holder(workshop):
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    b = _open_a_worktree(workshop, "thread-b", "Sidebar width")
    for tree, holder in ((a, "thread-a"), (b, "thread-b")):
        workshop["leases"].acquire("worktree",
                                   tree_access.subject_id_for(tree["dir"]),
                                   holder_kind="chat", holder_id=holder)

    assert tree_access.decide(a["dir"], session_id="thread-a").mode == "rw"
    assert tree_access.decide(b["dir"], session_id="thread-b").mode == "rw"
    assert tree_access.decide(a["dir"], session_id="thread-b").mode == "ro"
    # The shared tree is nobody's, so nobody writes it.
    assert tree_access.decide(str(workshop["proj"]),
                              session_id="thread-a").mode == "ro"


@needs_bwrap
def test_a_second_chat_cannot_write_the_first_chats_tree(workshop):
    """The clause the whole design exists for, as errno 30 from the kernel."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    workshop["leases"].acquire("worktree", tree_access.subject_id_for(a["dir"]),
                               holder_kind="chat", holder_id="thread-a")

    ok = _in_sandbox(a["dir"], "thread-a",
                     "echo mine > /workspace/toolbar.txt && echo WROTE")
    refused = _in_sandbox(a["dir"], "thread-b",
                          "echo theirs > /workspace/toolbar.txt && echo WROTE")

    assert ok[0] == 0 and ok[1] == "WROTE"
    assert refused[0] != 0
    assert tree_access.detect_erofs(refused[2]), (
        f"the refusal was not recognised as the first-write event: {refused[2]!r}")
    # And the holder's file is untouched, not merely un-overwritten by luck.
    assert (open(os.path.join(a["dir"], "toolbar.txt")).read().strip() == "mine")


@needs_bwrap
def test_neither_chat_can_even_see_the_others_tree(workshop):
    """"does not see or clobber" -- the seeing half."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    b = _open_a_worktree(workshop, "thread-b", "Sidebar width")
    workshop["leases"].acquire("worktree", tree_access.subject_id_for(b["dir"]),
                               holder_kind="chat", holder_id="thread-b")
    open(os.path.join(a["dir"], "secret.txt"), "w").write("thread-a's work\n")

    listing = _in_sandbox(b["dir"], "thread-b", "ls /workspace")
    reach = _in_sandbox(b["dir"], "thread-b", f"cat {a['dir']}/secret.txt")

    assert "secret.txt" not in listing[1]
    assert reach[0] != 0, "one chat read another chat's file off the host path"


@needs_bwrap
def test_the_holder_can_commit_from_inside_the_sandbox(workshop):
    """A linked worktree keeps its objects in the parent repo, so a tree bound
    alone is not a repository at all. `scoped_exec._git_worktree_binds` is what
    makes this pass, and nothing else exercised it end to end."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    workshop["leases"].acquire("worktree", tree_access.subject_id_for(a["dir"]),
                               holder_kind="chat", holder_id="thread-a")

    rc, out, err = _in_sandbox(
        a["dir"], "thread-a",
        "cd /workspace && echo work > done.txt && git add -A && "
        "git -c user.email=t@example.test -c user.name=t "
        "-c commit.gpgsign=false commit -qm 'work from the chat' && "
        "git log --oneline -1")

    assert rc == 0, f"git could not run inside the worktree sandbox: {err}"
    assert "work from the chat" in out


# --- nobody opts in -----------------------------------------------------------

@needs_bwrap
def test_the_first_write_makes_the_worktree_and_nobody_asked_for_it(workshop,
                                                                    monkeypatch):
    """The clause the last six worktrees died of.

    Six trees were made on 2026-08-22/23, all merged cleanly, and none since --
    because the only trigger that was ever armed was the autonomous sweep, whose
    last iteration was 2026-08-23 13:22. The interactive trigger
    (`_maybe_write_blocked` -> `_auto_create_worktree_for_chat`) is the first
    line of that function, behind `WORKSHOP_TREE_LEASE_MOUNT`, and has therefore
    never run once in production. An opt-in gets abandoned; this asserts there
    is nothing to opt into.

    The EROFS is REAL. A chat with no worktree resolves to the shared tree, the
    mount refuses its write because it holds no lease, and the string the tool
    prints is what the platform gets to work with -- so the string is produced
    by bwrap here rather than written by this test.
    """
    from services import build_streaming, chat_context, chat_store

    rc, _out, err = _in_sandbox(str(workshop["proj"]), "thread-a",
                                "echo edit > /workspace/README.md && echo WROTE")
    assert rc != 0, "the shared tree was writable by a chat holding no lease"

    pinned: list = []
    monkeypatch.setattr(chat_store, "get_thread", lambda tid: _thread(
        tid, "Chat toolbar fixes"))
    monkeypatch.setattr(chat_store, "set_thread_branch",
                        lambda tid, slug: pinned.append((tid, slug)) or {})
    monkeypatch.setattr(chat_context, "get_provider",
                        lambda key: workshop["provider"])
    said: list = []
    monkeypatch.setattr(build_streaming, "write_to_turn",
                        lambda tid, text, *a, **kw: said.append(text))

    turn = build_streaming.Turn(id="turn-1", thread_id="thread-a",
                                agent_slug="probe")
    build_streaming._maybe_write_blocked(turn, {"thread_id": "thread-a"},
                                         {"text": err})

    assert turn.write_offer is not None, (
        "a real EROFS did not produce a worktree; nothing else would have")
    assert turn.write_offer["branch_slug"] == "chat-toolbar-fixes"
    assert pinned == [("thread-a", "chat-toolbar-fixes")], "the chat was not pinned"
    assert said and "chat-toolbar-fixes" in said[0].lower().replace(" ", "-") or \
        "Chat toolbar fixes" in said[0]
    # And the tree it was given is real, and is the one the next turn resolves to.
    made = worktrees.branch_dir("probe", "chat-toolbar-fixes")
    assert os.path.isdir(made)
    nxt = chat_worktree.plan(_thread("thread-a", "Chat toolbar fixes",
                                     "chat-toolbar-fixes"),
                             workshop["provider"],
                             fallback_cwd=str(workshop["proj"]))
    assert nxt.cwd == str(made), "the next turn does not run in the tree just made"


# --- the return trip ----------------------------------------------------------

def test_an_unpinned_chat_is_told_there_is_nothing_to_merge(workshop):
    state = chat_worktree.diff(_thread("thread-a", "Just asking"),
                               workshop["provider"],
                               fallback_cwd=str(workshop["proj"]))

    assert state["pinned"] is False
    assert state["mergeable"] is False
    assert "shared tree" in state["reason"]


def test_the_diff_names_the_files_and_the_ref_it_would_merge_into(workshop):
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    open(os.path.join(a["dir"], "toolbar.txt"), "w").write("mine\n")
    _commit_all(a["dir"], "toolbar work")

    state = chat_worktree.diff(_thread("thread-a", "Chat toolbar fixes",
                                       a["branch_slug"]),
                               workshop["provider"],
                               fallback_cwd=str(workshop["proj"]))

    assert state["mergeable"] is True
    assert state["base_ref"] == "main"
    assert state["ahead"] == 1
    assert [f["path"] for f in state["files"]] == ["toolbar.txt"]


def test_the_work_merges_back_and_the_chat_goes_home(workshop):
    """The last clause: reviewed and merged back."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    open(os.path.join(a["dir"], "toolbar.txt"), "w").write("mine\n")
    _commit_all(a["dir"], "toolbar work")
    unpinned: list = []

    from services import chat_store
    import unittest.mock as mock
    with mock.patch.object(chat_store, "set_thread_branch",
                           side_effect=lambda tid, slug: unpinned.append((tid, slug)) or {}):
        result = chat_worktree.merge(
            "thread-a", _thread("thread-a", "Chat toolbar fixes", a["branch_slug"]),
            workshop["provider"], fallback_cwd=str(workshop["proj"]),
            message="toolbar fixes")

    assert result["ok"] is True, result["reason"]
    assert (workshop["proj"] / "toolbar.txt").read_text() == "mine\n"
    assert not os.path.isdir(a["dir"]), "the merged worktree was left on disk"
    assert unpinned == [("thread-a", None)], "the chat is still pinned to a tree that is gone"


def test_uncommitted_work_is_committed_rather_than_reaped_with_the_tree(workshop):
    """A merge deletes the directory, so anything uncommitted would go with it.
    Committing is the only one of the three options that loses nothing."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    open(os.path.join(a["dir"], "unsaved.txt"), "w").write("never committed\n")

    from services import chat_store
    import unittest.mock as mock
    with mock.patch.object(chat_store, "set_thread_branch", return_value={}):
        result = chat_worktree.merge(
            "thread-a", _thread("thread-a", "Chat toolbar fixes", a["branch_slug"]),
            workshop["provider"], fallback_cwd=str(workshop["proj"]),
            message="toolbar fixes")

    assert result["ok"] is True, result["reason"]
    assert result["committed"] == ["unsaved.txt"]
    assert (workshop["proj"] / "unsaved.txt").read_text() == "never committed\n"


def test_a_merge_will_not_move_a_dirty_project_tree_underneath_anyone(workshop):
    """`merge_branch` checks the base ref out in the project's OWN tree, and git
    carries uncommitted changes across a checkout when they do not conflict. So
    a dirty canonical tree is the one hard refusal."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    open(os.path.join(a["dir"], "toolbar.txt"), "w").write("mine\n")
    _commit_all(a["dir"], "toolbar work")
    (workshop["proj"] / "someone-elses-wip.txt").write_text("in progress\n")

    result = chat_worktree.merge(
        "thread-a", _thread("thread-a", "Chat toolbar fixes", a["branch_slug"]),
        workshop["provider"], fallback_cwd=str(workshop["proj"]))

    assert result["ok"] is False
    assert "someone-elses-wip.txt" in result["reason"]
    assert os.path.isdir(a["dir"]), "the tree was reaped despite the refusal"


# --- the operator's override --------------------------------------------------

def test_a_direct_operator_message_takes_the_tree_and_snapshots_the_holder(workshop):
    """His rule, verbatim: "if the user is in a chat and it is user operated ...
    and it is directly executing a direct user message, then that should be able
    to override the lease." An attended turn preempts; the displaced holder's
    uncommitted work is snapshotted first, so taking the tree loses nothing."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    subject = tree_access.subject_id_for(a["dir"])
    workshop["leases"].acquire("worktree", subject, holder_kind="run",
                               holder_id="job-99")
    open(os.path.join(a["dir"], "half-done.txt"), "w").write("mid-edit\n")

    taken = chat_worktree.enter(
        "thread-a", _thread("thread-a", "Chat toolbar fixes", a["branch_slug"]),
        workshop["provider"], fallback_cwd=str(workshop["proj"]), preempt=True)

    assert taken.held, "the operator's own message did not get the tree"
    assert taken.preempted["holder_id"] == "job-99"
    assert taken.preempted["wip_ref"] == "refs/wip/job-99"
    assert git_service.rev_parse(str(workshop["proj"]), "refs/wip/job-99",
                                 verify=True), "nothing was snapshotted"
    assert tree_access.decide(a["dir"], session_id="thread-a").mode == "rw"


def test_an_unattended_run_queues_instead_of_taking_it(workshop):
    """The other half of the same rule: an agent never preempts an agent."""
    a = _open_a_worktree(workshop, "thread-a", "Chat toolbar fixes")
    workshop["leases"].acquire("worktree", tree_access.subject_id_for(a["dir"]),
                               holder_kind="chat", holder_id="thread-b")

    blocked = chat_worktree.enter(
        "thread-a", _thread("thread-a", "Chat toolbar fixes", a["branch_slug"]),
        workshop["provider"], fallback_cwd=str(workshop["proj"]), preempt=False)

    assert not blocked.held
    assert blocked.blocked_by["holder_id"] == "thread-b"
    assert tree_access.decide(a["dir"], session_id="thread-a").mode == "ro"
