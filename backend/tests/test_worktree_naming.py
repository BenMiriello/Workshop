"""A worktree is named after its work, and it is created either way.

§3 of `docs/platform-redesign/worktree-as-the-place-2026-08-28.md`: "no worktree
is ever created without a human-readable name". `test_worktree_slug.py` covers
`_kebab_branch_slug` as a pure function; this file covers the thing the operator
actually meets -- the branch that ends up on disk -- because the defect was never
in the slugifier. It was in the callers, which had the title and did not pass it.

REAL GIT, NO MOCKS, for the reason `test_worktree_records.py` gives: an
allocator stubbed to return a name would happily report `chat-toolbar-fixes`
while `git worktree add` made something else, which is the exact failure mode
under test. The record half is best-effort by construction (no Postgres is not
an allocation failure), so these run without a database and assert on the
branch, the ref and the directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.test",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.test",
}


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *args], check=True,
                         capture_output=True, text=True,
                         env={**os.environ, **_GIT_ENV})
    return res.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True,
                   capture_output=True, env={**os.environ, **_GIT_ENV})
    (path / "README.md").write_text("base\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real git project registered as `proj`, with its own branches root."""
    import services.worktrees as wt_mod

    proj_dir = tmp_path / "projects" / "proj"
    _init_repo(proj_dir)

    branches = tmp_path / "branches"
    branches.mkdir()
    monkeypatch.setattr(wt_mod, "BRANCHES_ROOT", branches)

    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"proj": {"dir": str(proj_dir)}}}))
    monkeypatch.setattr(wt_mod, "PROJECTS_REGISTRY", registry)
    return proj_dir


def _branches(project_dir: Path) -> set[str]:
    out = _git(project_dir, "for-each-ref", "--format=%(refname:short)",
               "refs/heads/wt/")
    return {line for line in out.splitlines() if line}


# ---------------------------------------------------------------------------
# a titled ticket gets a readable branch
# ---------------------------------------------------------------------------

def test_a_titled_ticket_gets_a_readable_branch(project):
    """The headline claim of §3, asserted on the git ref rather than on a dict.

    The ticket id is a 32-char hex uuid -- exactly what used to become the
    branch name -- so a slug derived from the id would be visible here.
    """
    from services import worktrees

    ticket_id = "30777f91367b4c7b862df419cc9a36e2"
    result = worktrees.allocate_task_worktree(
        "proj", ticket_id, title="Chat toolbar fixes")

    assert result["branch"] == "wt/proj/chat-toolbar-fixes"
    assert result["branch_slug"] == "chat-toolbar-fixes"
    assert result["display_name"] == "Chat toolbar fixes"
    assert Path(result["dir"]).name == "chat-toolbar-fixes"
    assert Path(result["dir"]).is_dir()
    # The ref is real, not just the record's opinion of it.
    assert "wt/proj/chat-toolbar-fixes" in _branches(project)
    assert ticket_id not in result["branch"]


def test_the_offer_path_names_the_tree_after_the_chat(project):
    """`chat_worktree.apply_choice` is the caller that was still minting hex.

    Its work id is `uuid4().hex[:8]`, which is where `t-26c69d40` and
    `d4875115` on this installation came from. Given the chat's title it must
    produce the same readable branch the ticket path does.
    """
    from services import chat_worktree

    offer = chat_worktree.pin_offer("proj", current_ref="main")
    created = chat_worktree.apply_choice(
        "proj", offer, "new_branch", title="Chat toolbar fixes")

    assert created["created"] is True
    assert created["branch"] == "wt/proj/chat-toolbar-fixes"
    assert created["display_name"] == "Chat toolbar fixes"
    assert "wt/proj/chat-toolbar-fixes" in _branches(project)


# ---------------------------------------------------------------------------
# two tickets, one title
# ---------------------------------------------------------------------------

def test_two_tickets_with_the_same_title_do_not_collide(project):
    """Both trees exist, at different names, and neither creation raised.

    The suffix is deterministic per (title, task id), so a retry of the second
    allocation asks for the name it already has rather than accumulating trees.
    """
    from services import worktrees

    first = worktrees.allocate_task_worktree(
        "proj", "aaaaaaaa1111111111111111111111aa", title="Fix the toolbar")
    second = worktrees.allocate_task_worktree(
        "proj", "bbbbbbbb2222222222222222222222bb", title="Fix the toolbar")

    assert first["branch_slug"] == "fix-the-toolbar"
    assert second["branch_slug"] != first["branch_slug"]
    assert second["branch_slug"].startswith("fix-the-toolbar-")
    # Both are readable: the disambiguator is a suffix, not a replacement.
    assert "fix-the-toolbar" in second["branch_slug"]
    # Both trees are really there, at their own directories.
    assert Path(first["dir"]).is_dir()
    assert Path(second["dir"]).is_dir()
    assert Path(first["dir"]) != Path(second["dir"])
    assert _branches(project) >= {f"wt/proj/{first['branch_slug']}",
                                  f"wt/proj/{second['branch_slug']}"}
    # Both keep the human name; the slug disambiguates, the display name does not.
    assert first["display_name"] == second["display_name"] == "Fix the toolbar"


def test_a_third_ticket_with_the_same_title_still_gets_a_name(project):
    """One collision is the easy case. The loop has to survive the next one."""
    from services import worktrees

    slugs = {
        worktrees.allocate_task_worktree(
            "proj", f"{n:032x}", title="Fix the toolbar")["branch_slug"]
        for n in range(3)
    }
    assert len(slugs) == 3
    assert all(s.startswith("fix-the-toolbar") for s in slugs)


def test_the_disambiguated_slug_is_deterministic(project, tmp_path,
                                                 monkeypatch):
    """The same (title, task id) pair lands on the same name every time.

    Asserted by asking the naming rule twice with the first tree in place:
    a random or counter-based suffix would answer differently the second time.
    """
    from services import worktrees

    worktrees.allocate_task_worktree("proj", "aaaaaaaa1111", title="Same name")
    once = worktrees.branch_slug_for_work("proj", title="Same name",
                                          work_id="bbbbbbbb2222")
    twice = worktrees.branch_slug_for_work("proj", title="Same name",
                                           work_id="bbbbbbbb2222")
    assert once == twice != "same-name"


# ---------------------------------------------------------------------------
# no title available
# ---------------------------------------------------------------------------

def test_a_worktree_with_no_title_is_still_created_and_addressable(project):
    """A nameless unit of work beats no tree, and is still not named after hex.

    The rule is "never created without a human-readable name", not "never
    created": when nothing can supply a title the tree is still made, but it is
    called `unnamed-<short>` rather than the raw id. This assertion used to read
    `t-30777f91367b4c7b`, which is the defect -- the tree has to be reachable
    through every lookup that takes a branch slug AND has to be readable in the
    list where the operator meets it.
    """
    from services import worktrees

    result = worktrees.allocate_task_worktree("proj", "30777f91367b4c7b")

    assert result["branch_slug"] == "unnamed-30777f91"
    assert not worktrees.reads_as_machine_id(result["branch_slug"])
    assert result["display_name"] is None
    assert Path(result["dir"]).is_dir()

    slug = result["branch_slug"]
    # Addressable: the path helper, the git ref, the disk listing and the
    # branch-state read all resolve it. THESE ARE KEYED ON THE RECORD/SLUG, not
    # on parsing the name, which is what keeps the eleven pre-existing trees on
    # disk working after the naming change.
    assert worktrees.branch_dir("proj", slug) == Path(result["dir"])
    assert f"wt/proj/{slug}" in _branches(project)
    listed = {b["branch_slug"] for b in worktrees.list_branches("proj")}
    assert slug in listed
    state = worktrees.branch_state("proj", slug)
    assert state["branch"] == f"wt/proj/{slug}"


def test_an_empty_title_falls_back_to_the_id_rather_than_failing(project):
    """A chat with a blank title is a real state, not a caller error.

    A blank title is not a title, so this takes the unnamed path and gets a name
    from it. What it must NOT do is raise: the chat has already asked to write.
    """
    from services import worktrees

    result = worktrees.allocate_task_worktree("proj", "abc123def", title="   ")

    assert result["branch_slug"] == "unnamed-abc123de"
    assert Path(result["dir"]).is_dir()


def test_an_id_that_is_already_readable_is_kept_whole(project):
    """An id can be a name. `sec-a6-harden-...` was one of the six on disk."""
    from services import worktrees

    result = worktrees.allocate_task_worktree("proj", "sec-a6-harden-containers")

    assert result["branch_slug"] == "sec-a6-harden-containers"
    assert Path(result["dir"]).is_dir()


def test_an_untitled_id_reused_still_refuses_rather_than_forking(project):
    """The id path keeps its contract: one id names one tree.

    Disambiguation is for titles, which legitimately repeat across two units of
    work. Two allocations under the same id are the same allocation twice, and
    silently making a second tree would hide that.
    """
    from services import worktrees

    worktrees.allocate_task_worktree("proj", "only-once")
    with pytest.raises(ValueError, match="already exists"):
        worktrees.allocate_task_worktree("proj", "only-once")


# ---------------------------------------------------------------------------
# the existing trees keep working
# ---------------------------------------------------------------------------

def test_a_hex_named_tree_made_before_the_change_is_still_resolvable(project):
    """The pre-existing worktrees are addressed by slug and must stay so.

    THE READ SIDE STAYS PERMISSIVE while the write side stops. This tree is made
    with raw git, the way the code that produced `a89d3bd46dfc48c6bcfe4ca49c6399
    62` on this installation did, because `create_branch` now refuses that name
    and `allocate_task_worktree` no longer derives it -- so the only honest way
    to get one is to put it there. Then it is read back through the same lookups
    a chat pinned to it uses. Nothing here parses the name, which is what keeps
    a record or a pinned chat that still cites a legacy slug working.
    """
    from services import worktrees

    slug = "a89d3bd46dfc48c6bcfe4ca49c639962"
    target = worktrees.BRANCHES_ROOT / "proj" / slug
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(project, "worktree", "add", "-b", f"wt/proj/{slug}", str(target))

    assert worktrees.branch_dir("proj", slug).is_dir()
    assert slug in {b["branch_slug"] for b in worktrees.list_branches("proj")}
    live = worktrees.live_branches(project_slug="proj")["worktrees"]
    assert any(b["branch_slug"] == slug and b["project_slug"] == "proj"
               for b in live)

    # And a new, titled tree in the same project does not disturb it.
    worktrees.allocate_task_worktree("proj", "t-999", title="Something else")
    assert worktrees.branch_dir("proj", slug).is_dir()


# ---------------------------------------------------------------------------
# the proof: no path emits a branch named after a machine id
# ---------------------------------------------------------------------------
#
# THIS IS THE CLAIM, and it is a claim about EVERY WRITER, so it is asserted
# against every writer rather than against the one that was reported. The
# operator reported the symptom on the branch list -- "all of the branches below
# have just a long sort of string of letters and numbers as the name" -- and the
# previous fix changed the one caller that was known to have a title. It left
# `t-<hex>` and BARE `<hex>` reachable from two other doors, which is why three
# of the six trees on this installation still carry hex names today.
#
# There are exactly three doors into a branch name and they are enumerated here:
#   1. `branch_slug_for_work` -- the ticket path and the chat offer path
#   2. `create_branch`        -- the no-ticket path and the HTTP route
#   3. `allocate_task_worktree` -- which goes through (1) and is asserted anyway,
#      because "it calls the safe one" is exactly the assumption that broke.
# `provision_worktree` is not a fourth: it forwards to (3) with the same title
# and work id, adding only the dependency clone.

def test_no_creation_path_can_emit_a_branch_named_after_a_machine_id(project):
    """Every writer, against every shape of id that has been seen on disk."""
    from services import worktrees

    # The exact strings that ended up as directory names under
    # branches/development/, plus the two shapes they generalise to.
    machine_ids = [
        "a89d3bd46dfc48c6bcfe4ca49c639962",   # bare 32-hex: starts with a letter
        "bc81b563abe14333be4e8626dc8182bd",
        "d8676bbabd924e7e8d4e6d18d48dbcdc",
        "cb311b87376f46dbab5203a5da5806d4",
        "30777f91367b4c7b862df419cc9a36e2",
        "26c69d40",                            # uuid4().hex[:8], the offer path
        "0346adcc66784569bd9051ed12e3617b",    # starts with a digit -> `t-` shape
        "4a79ffc515844ca2a48a369b7ca7b0a7",
    ]

    for i, work_id in enumerate(machine_ids):
        # Door 1 and 3: an untitled unit of work.
        slug = worktrees.branch_slug_for_work("proj", work_id=work_id)
        assert not worktrees.reads_as_machine_id(slug), (
            f"branch_slug_for_work named a branch after the id {work_id!r}: {slug!r}")

        allocated = worktrees.allocate_task_worktree(f"proj", work_id)
        assert not worktrees.reads_as_machine_id(allocated["branch_slug"])
        assert not worktrees.reads_as_machine_id(Path(allocated["dir"]).name)
        # The branch is never simply the id. A short id survives INSIDE
        # `unnamed-26c69d40`, which is the point -- the tree stays traceable to
        # its work -- but it is no longer what the branch is called.
        assert allocated["branch"] != f"wt/proj/{work_id}"
        assert allocated["branch_slug"] != worktrees.branch_slug_for(work_id)

        # And a title that is ITSELF an id is not a name either: it takes the
        # same door and must not get through it on the strength of being passed
        # in the title argument.
        titled = worktrees.branch_slug_for_work(
            "proj", title=work_id, work_id=f"other{i}")
        assert not worktrees.reads_as_machine_id(titled)

    # Door 2: a slug handed in from outside, which is what the HTTP route does.
    for work_id in machine_ids:
        with pytest.raises(ValueError, match="machine id"):
            worktrees.create_branch("proj", worktrees.branch_slug_for("t-" + work_id))
        with pytest.raises(ValueError, match="machine id"):
            worktrees.create_branch("proj", worktrees.branch_slug_for(work_id))


def test_the_route_that_takes_a_slug_refuses_a_machine_id(project):
    """`routes/worktrees.py` hands `create_branch` the request body verbatim.

    Asserted at the service boundary rather than over HTTP because that is where
    the refusal lives and where a future second route would meet it too.
    """
    from services import worktrees

    with pytest.raises(ValueError, match="machine id"):
        worktrees.create_branch("proj", "a89d3bd46dfc48c6bcfe4ca49c639962")

    # A real name through the same door is unaffected.
    made = worktrees.create_branch("proj", "chat-toolbar-fixes")
    assert made["branch_slug"] == "chat-toolbar-fixes"


def test_reads_as_machine_id_is_the_one_definition(project):
    """The predicate itself, since both refusals are keyed on it.

    A name with words in it is a name however much hex it contains, and a short
    id is not a wall -- `branchName` in the frontend would show either in full.
    """
    from services import worktrees

    for value in ("a89d3bd46dfc48c6bcfe4ca49c639962", "t-26c69d40",
                  "abc123def", "deadbeef", "0123456789abcdef"):
        assert worktrees.reads_as_machine_id(value), value

    for value in ("chat-toolbar-fixes", "unnamed-a89d3bd4", "sec-a6-harden",
                  "t-999", "t-42", "fix-the-toolbar", "faced"):
        assert not worktrees.reads_as_machine_id(value), value
