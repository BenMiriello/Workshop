"""Queue, steer and preempt: the three verbs, and what preempt does to a tree.

Steps B6 and B7 of
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(sections 8 and 9).

What must be true:

  - queue goes through the queue that ALREADY EXISTS (`chat_queue`), is FIFO,
    and is cancellable;
  - the steer copy never promises immediacy -- section 8: "the UI must not label
    this `now`" -- because the measured behaviour is that the model addresses a
    steered message at the end of its current plan;
  - a preempt with no live human action behind it is REFUSED, because authority
    is bound to a human action and not to a session kind: an agent may never
    preempt another agent, it queues or asks;
  - a preempt SNAPSHOTS TO A DANGLING REF and the branch tip is unchanged
    (asserted as a sha, both sides), and the working tree is clean afterwards;
  - a stale `.git/index.lock` left by a killed git is cleared BEFORE the lease
    is granted;
  - the timeline records each event with its fields.

THE GIT HALF RUNS AGAINST A REAL REPOSITORY in tmp_path. There is no way to test
"the branch did not move" against a mock: the whole claim is about what git did.

THE LEASE HALF RUNS AGAINST A FAKE, and deliberately: `services/leases.py` needs
Postgres and is tested against a real database in `test_leases.py`. What is
under test here is the ORDER of the six steps -- that the lock is cleared and
the snapshot taken before `acquire` is ever called -- which is a property of
this module and is invisible to a database test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import control, git_service, run_control  # noqa: E402


# --- fixtures ----------------------------------------------------------------

@pytest.fixture()
def channel(tmp_path, monkeypatch):
    d = tmp_path / "control"
    monkeypatch.setenv("WORKSHOP_CONTROL_DIR", str(d))
    return d


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    """A chat store in tmp_path, the fixture `test_chat_queue` uses."""
    from services import ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def thread(owned):
    from services import chat_store
    return chat_store.create_thread("general")["id"]


@pytest.fixture()
def repo(tmp_path):
    """A real git repository with one commit on `main`."""
    d = tmp_path / "tree"
    d.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q", "-b", "main", "."],):
        subprocess.run(["git", "-C", str(d), *args], check=True, env=env)
    (d / "kept.txt").write_text("original\n")
    (d / ".gitignore").write_text("heavy/\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "init"], check=True,
                   env=env)
    return d


class FakeLeases:
    """The lease table, in a dict, with `LeasesUnavailable` kept real.

    Only the five calls `services/control.py` makes; anything else would be
    inventing behaviour the real module has to be trusted for.
    """

    LeasesUnavailable = Exception
    PREEMPT_GRACE_SEC = 30

    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple] = []

    def get(self, subject_kind, subject_id):
        self.calls.append(("get", subject_kind, subject_id))
        return dict(self.row) if self.row else None

    def fence(self, subject_kind, subject_id):
        self.calls.append(("fence", subject_kind, subject_id))
        if self.row:
            self.row["generation"] += 1
            self.row["state"] = "expired"
        return (self.row or {}).get("generation")

    def acquire(self, subject_kind, subject_id, *, holder_kind, holder_id,
                **kw):
        self.calls.append(("acquire", subject_kind, subject_id, holder_id))
        self.row = {"subject_kind": subject_kind, "subject_id": subject_id,
                    "holder_kind": holder_kind, "holder_id": holder_id,
                    "generation": ((self.row or {}).get("generation") or 0) + 1,
                    "state": "held", "principal_user_id": None,
                    "idle_since": None, "expires_at": None,
                    "acquired_at": None, "last_renewed_at": None,
                    "last_work_at": None}
        return dict(self.row)


def _held(holder_id="sweep-42", holder_kind="run", generation=3):
    return {"subject_kind": "worktree", "subject_id": "wt-1",
            "holder_kind": holder_kind, "holder_id": holder_id,
            "generation": generation, "state": "held",
            "principal_user_id": None, "idle_since": None,
            "expires_at": None, "acquired_at": None,
            "last_renewed_at": None, "last_work_at": None}


async def _stubbed_stop(holder_id, holder_kind):
    """`_force_stop` without the signals. It is AWAITED by `preempt`, which is
    what keeps the quiesce from snapshotting a tree a live process is writing,
    so a stub for it has to be a coroutine too."""
    return {"stopped": True, "how": "stubbed", "pid": None}


@pytest.fixture()
def events(monkeypatch):
    """Capture what lands on the worktree timeline, with its fields."""
    from services import worktree_records
    seen: list[dict] = []

    def add_event(worktree_id, *, kind, actor=None, sha=None, detail=None,
                  paths=None):
        seen.append({"worktree_id": worktree_id, "kind": kind, "actor": actor,
                     "sha": sha, "detail": detail, "paths": paths})
        return seen[-1]

    monkeypatch.setattr(worktree_records, "add_event", add_event)
    return seen


# --- queue -------------------------------------------------------------------

def test_a_chat_request_goes_onto_the_queue_that_already_exists(thread, channel):
    from services import chat_queue
    out = control.queue(thread, "look at this when you are done",
                        holder_kind="chat", request_id="r-1")
    assert out["queued"] is True and out["delivered"] is False
    # THE SAME STORE the composer writes: not a second queue beside it.
    assert [e["text"] for e in chat_queue.read(thread)] == [
        "look at this when you are done"]
    assert chat_queue.queue_path(thread).exists()


def test_queued_requests_are_fifo_and_cancellable(thread, channel):
    for i, text in enumerate(("first", "second", "third")):
        control.queue(thread, text, holder_kind="chat", request_id=f"r-{i}")
    assert [r["text"] for r in control.pending(thread, holder_kind="chat")] == [
        "first", "second", "third"]
    assert control.cancel(thread, "r-1", holder_kind="chat") is True
    assert [r["text"] for r in control.pending(thread, holder_kind="chat")] == [
        "first", "third"]
    assert control.cancel(thread, "r-1", holder_kind="chat") is False


def test_a_run_request_goes_onto_the_control_file(channel):
    out = control.queue("sweep-42", "when you finish, run the tests",
                        holder_kind="run", actor="operator")
    assert out["holder_kind"] == "run"
    assert [r["text"] for r in run_control.pending("sweep-42")] == [
        "when you finish, run the tests"]


def test_an_empty_request_is_refused(channel):
    with pytest.raises(control.ControlRefused):
        control.queue("sweep-42", "   ", holder_kind="run")


# --- steer -------------------------------------------------------------------

def test_a_steer_at_a_sweep_is_folded_into_its_next_iteration(channel, events):
    out = control.steer("sweep-42", "also update the docs", actor="operator",
                        holder_kind="run", worktree_id="wt-1")
    assert out["delivered"] is False
    assert [r["text"] for r in run_control.pending("sweep-42")] == [
        "also update the docs"]
    # Section 9: the record is a structured event, not prose in a transcript.
    assert events == [{"worktree_id": "wt-1", "kind": "steered",
                       "actor": "operator", "sha": None,
                       "detail": "also update the docs", "paths": None}]


def test_a_steer_with_no_turn_to_reach_falls_back_to_the_queue(thread, channel):
    from services import chat_queue
    out = control.steer(thread, "one more thing", holder_kind="chat")
    assert out["verb"] == "steer"
    assert out["delivered"] is False and out["queued"] is True
    assert [e["text"] for e in chat_queue.read(thread)] == ["one more thing"]


def test_a_steer_into_a_live_turn_reports_delivery_and_nothing_more(
        thread, channel, monkeypatch):
    from services import build_streaming, chat_store
    monkeypatch.setattr(chat_store, "active_turn_id", lambda tid: "turn-1")
    monkeypatch.setattr(build_streaming, "turn_is_live", lambda tid: True)
    monkeypatch.setattr(build_streaming, "inject",
                        lambda turn_id, text, **kw: True)
    out = control.steer(thread, "also check the logs", holder_kind="chat")
    # `delivered` means the bytes reached the turn. It is the only thing this
    # system can observe, and the copy beside it must not claim more.
    assert out["delivered"] is True


@pytest.mark.parametrize("copy", [control.STEER_COPY_CHAT,
                                  control.STEER_COPY_CHAT_QUEUED,
                                  control.STEER_COPY_RUN,
                                  control.QUEUE_COPY])
def test_no_user_facing_string_promises_immediacy(copy):
    """Section 8: "The UI must not label this `now`."

    Revision 1 sold steer as "delivered at the next safe boundary... in
    seconds", which is the behaviour this repo measured and found false
    (`chat-message-ordering-audit-2026-08-05.md` section 2).
    """
    lowered = copy.lower()
    for word in ("now", "immediately", "instantly", "right away",
                 "next tool", "next step", "at once", "in seconds"):
        assert word not in lowered, f"{copy!r} promises {word!r}"


# --- preempt: the authority rule --------------------------------------------

@pytest.mark.parametrize("human_action", ["", "   ", None])
def test_preempt_without_a_human_action_is_refused(human_action):
    """Section 8's authority rule, which revision 1 got wrong.

    It said the operator "and any chat acting on his behalf" may preempt, which
    swallows the rule it was next to, because EVERY chat acts on his behalf. So
    the requirement is carried by the call and not by the caller's kind: an
    agent has no honest value to put here, and queue and steer are beside it.
    """
    with pytest.raises(control.HumanActionRequired):
        _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                             actor="agent:maker", human_action=human_action))


def test_the_refusal_says_what_to_do_instead():
    try:
        _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                             actor="agent:maker", human_action=""))
    except control.HumanActionRequired as e:
        assert "queues" in str(e) and "asks" in str(e)
    else:  # pragma: no cover - the test above already pins the raise
        pytest.fail("a preempt with no human action was allowed")


def test_there_is_no_allowlist_of_session_kinds_anywhere_in_the_verb():
    """The rule must not be re-expressed as "chats may, runs may not".

    A session kind cannot carry it (every chat acts on the operator's behalf),
    and a permission matrix is what CLAUDE.md forbids for a single-operator
    install. So the CODE of the verb names no kind as privileged -- the prose
    around it is free to say what it is not, which is why this reads the parsed
    body with its docstring removed rather than grepping the file.
    """
    import ast
    tree = ast.parse(Path(control.__file__).read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "preempt")
    body = list(fn.body)
    if (isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    code = "\n".join(ast.unparse(n) for n in body)
    for banned in ("allowlist", "allow_list", "is_operator", "role ==",
                   "permission", "may_preempt", "priority"):
        assert banned not in code, f"the verb decides on {banned!r}"
    # The one thing it DOES require, and it is an argument the call carries.
    assert "human_action" in code


# --- preempt: the quiesce ----------------------------------------------------

def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _dirty(repo: Path) -> None:
    (repo / "kept.txt").write_text("half applied\n")
    (repo / "new_file.txt").write_text("written by the holder\n")
    (repo / "heavy").mkdir(exist_ok=True)
    (repo / "heavy" / "dep").write_text("an app input\n")


def test_a_preempt_snapshots_to_a_dangling_ref_and_the_branch_tip_is_unchanged(
        repo, channel, monkeypatch, events):
    tip_before = git_service.rev_parse(repo, "main")
    head_before = git_service.rev_parse(repo, "HEAD")
    _dirty(repo)
    fake = FakeLeases(_held())
    monkeypatch.setattr(control, "leases", fake)
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)

    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator",
                               human_action="take-over confirmed in the lease card",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))

    assert out["preempted"] is True and out["forced"] is True
    q = out["quiesce"]
    # THE SHA THE DESIGN WANTS, on a ref reachable from nothing on the branch.
    assert q["snapshot_sha"] and q["snapshot_ref"] == "refs/wip/sweep-42"
    assert git_service.rev_parse(repo, "refs/wip/sweep-42") == q["snapshot_sha"]
    # THE BRANCH DID NOT MOVE, and neither did HEAD. Asserted as shas, from
    # both sides, because that is the entire correction section 9 makes.
    assert git_service.rev_parse(repo, "main") == tip_before
    assert git_service.rev_parse(repo, "HEAD") == head_before
    assert q["head_before"] == head_before and q["head_after"] == head_before
    assert git_service.rev_parse(repo, "HEAD", abbrev_ref=True) == "main"


def test_the_snapshot_holds_what_the_restore_then_deletes(repo, channel,
                                                          monkeypatch, events):
    _dirty(repo)
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    sha = out["quiesce"]["snapshot_sha"]
    listing = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", sha],
        capture_output=True, text=True, check=True).stdout.split()
    # The modification AND the untracked file, because the restore is about to
    # remove the untracked one: a tracked-only snapshot would destroy it.
    assert "kept.txt" in listing and "new_file.txt" in listing


def test_the_tree_is_clean_after_a_preempt_and_the_app_inputs_survive(
        repo, channel, monkeypatch, events):
    _dirty(repo)
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    assert out["quiesce"]["clean"] is True
    assert git_service.status(repo)["clean"] is True
    # Handing over a dirty tree is never acceptable...
    assert (repo / "kept.txt").read_text() == "original\n"
    assert not (repo / "new_file.txt").exists()
    # ...but an ignored directory is the app's heavy input, not the holder's
    # half-applied edit, and deleting it turns a handover into a re-provision.
    assert (repo / "heavy" / "dep").exists()


def test_a_stale_index_lock_is_cleared_before_the_lease_is_granted(
        repo, channel, monkeypatch, events):
    """Section 9: a killed git leaves `index.lock`, and every write fails until
    it goes -- including the snapshot. So it is cleared first, and in any case
    before the lease moves."""
    _dirty(repo)
    lock = Path(git_service.git_dir(repo)) / "index.lock"
    lock.write_text("")
    order: list[str] = []
    fake = FakeLeases(_held())
    real_acquire = fake.acquire

    def acquire(*a, **kw):
        order.append("acquire")
        assert not lock.exists(), (
            "the lease was granted while a killed git's index.lock was still "
            "there: the next holder inherits a tree it cannot write to")
        return real_acquire(*a, **kw)

    fake.acquire = acquire
    monkeypatch.setattr(control, "leases", fake)
    async def ordered_stop(hid, kind):
        order.append("stop")
        return {"stopped": True, "how": "", "pid": None}

    monkeypatch.setattr(control, "_force_stop", ordered_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    assert out["quiesce"]["cleared_index_lock"] is True
    assert not lock.exists()
    assert order == ["stop", "acquire"]
    # ...and the snapshot happened anyway, which is what the clearing bought.
    assert out["quiesce"]["snapshot_sha"]


def test_a_failed_snapshot_leaves_the_tree_alone(repo, channel, monkeypatch,
                                                 events):
    """The one case where a dirty tree is the right answer.

    Cleaning a tree whose contents were not recorded is the destruction this
    step exists to prevent, so a snapshot that fails must not be followed by a
    restore.
    """
    _dirty(repo)
    monkeypatch.setattr(git_service, "snapshot",
                        lambda *a, **kw: {"ok": False, "sha": None,
                                          "ref": None, "clean": False,
                                          "detail": "disk full"})
    q = control.quiesce(str(repo), session_id="sweep-42")
    assert q["ok"] is False and "disk full" in q["detail"]
    assert (repo / "new_file.txt").exists()
    assert (repo / "kept.txt").read_text() == "half applied\n"


def test_a_clean_tree_quiesces_to_no_snapshot_and_that_is_success(repo):
    q = control.quiesce(str(repo), session_id="sweep-42")
    assert q["ok"] is True and q["tree_was_clean"] is True
    assert q["snapshot_sha"] is None
    assert git_service.rev_parse(repo, "refs/wip/sweep-42") is None


# --- preempt: the grace, the lease and the timeline --------------------------

def test_a_holder_that_yields_inside_the_grace_is_never_forced(
        repo, channel, monkeypatch, events):
    fake = FakeLeases(_held())
    monkeypatch.setattr(control, "leases", fake)
    stops: list = []

    async def record_stop(hid, kind):
        stops.append(hid)
        return {"stopped": True, "how": "", "pid": None}

    monkeypatch.setattr(control, "_force_stop", record_stop)
    # The sweep read its control file and let go while the grace was running.
    real_get = fake.get

    def get(subject_kind, subject_id):
        row = real_get(subject_kind, subject_id)
        if len([c for c in fake.calls if c[0] == "get"]) > 1 and row:
            row["state"] = "expired"
        return row

    fake.get = get
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=2, repo_path=str(repo),
                               worktree_id="wt-1"))
    assert out["yielded"] is True and out["forced"] is False
    assert stops == []


def test_the_holder_is_asked_to_yield_before_the_grace_runs(repo, channel,
                                                            monkeypatch, events):
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                         actor="test-operator", human_action="take over",
                         reason="I need this branch", grace_sec=0,
                         repo_path=str(repo), worktree_id="wt-1"))
    asked = run_control.yield_requested("sweep-42")
    assert asked and asked["actor"] == "test-operator"
    assert asked["reason"] == "I need this branch"


def test_the_lease_is_fenced_and_then_granted_to_the_new_holder(
        repo, channel, monkeypatch, events):
    fake = FakeLeases(_held(generation=3))
    monkeypatch.setattr(control, "leases", fake)
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    verbs = [c[0] for c in fake.calls]
    assert verbs.index("fence") < verbs.index("acquire")
    assert out["lease"]["holder_id"] == "chat-9"
    # The generation moved past the displaced holder's, which is what makes a
    # resurrected zombie's write harmless.
    assert out["lease"]["generation"] > 3


def test_the_timeline_records_preempted_snapshot_and_acquired_with_their_fields(
        repo, channel, monkeypatch, events):
    _dirty(repo)
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator",
                               human_action="take-over confirmed in the lease card",
                               reason="I need this branch", grace_sec=0,
                               repo_path=str(repo), worktree_id="wt-1"))
    kinds = [e["kind"] for e in events]
    assert kinds == ["preempted", "snapshot", "acquired"]
    by_kind = {e["kind"]: e for e in events}
    # "preempted BY WHOM" (section 9), and why.
    assert by_kind["preempted"]["actor"] == "test-operator"
    assert by_kind["preempted"]["detail"] == "I need this branch"
    # "snapshot <sha>", carried in the sha field rather than written into prose.
    assert by_kind["snapshot"]["sha"] == out["quiesce"]["snapshot_sha"]
    assert by_kind["snapshot"]["detail"] == "refs/wip/sweep-42"
    assert by_kind["snapshot"]["actor"] == "sweep-42"
    assert by_kind["acquired"]["actor"] == "chat-9"


def test_the_timeline_kinds_are_the_closed_vocabulary(events, repo, channel,
                                                      monkeypatch):
    from db.models import WORKTREE_EVENT_KINDS
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    _dirty(repo)
    _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                         actor="test-operator", human_action="take over",
                         grace_sec=0, repo_path=str(repo), worktree_id="wt-1"))
    control.steer("sweep-42", "and this", holder_kind="run", worktree_id="wt-1")
    assert {e["kind"] for e in events} <= set(WORKTREE_EVENT_KINDS)


def test_preempting_a_subject_nobody_holds_just_grants_it(repo, channel,
                                                          monkeypatch, events):
    fake = FakeLeases(None)
    monkeypatch.setattr(control, "leases", fake)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    assert out["preempted"] is False and out["detail"] == "nobody was holding it"
    assert out["lease"]["holder_id"] == "chat-9"
    assert [e["kind"] for e in events] == ["acquired"]


def test_a_timeline_that_cannot_be_written_does_not_fail_the_verb(
        repo, channel, monkeypatch):
    from services import worktree_records

    def boom(*a, **kw):
        raise worktree_records.WorktreesUnavailable("no relational store")

    monkeypatch.setattr(worktree_records, "add_event", boom)
    monkeypatch.setattr(control, "leases", FakeLeases(_held()))
    monkeypatch.setattr(control, "_force_stop", _stubbed_stop)
    out = _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                               actor="test-operator", human_action="take over",
                               grace_sec=0, repo_path=str(repo),
                               worktree_id="wt-1"))
    # The record is how the event is REMEMBERED, not how it happens.
    assert out["preempted"] is True


def test_no_lease_store_is_not_swallowed_into_a_refusal(repo, channel,
                                                        monkeypatch):
    """An outage must not read as a decision.

    `services/changes.py` states the rule this follows: "nobody holds this"
    because the database is down must not read the same as "nobody holds this"
    because nobody does. So `LeasesUnavailable` travels up to the route, which
    answers 503.
    """
    from services import leases as real_leases

    class Down:
        LeasesUnavailable = real_leases.LeasesUnavailable

        def get(self, *a, **kw):
            raise real_leases.LeasesUnavailable("no relational store")

    monkeypatch.setattr(control, "leases", Down())
    with pytest.raises(real_leases.LeasesUnavailable):
        _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                             actor="test-operator", human_action="take over",
                             grace_sec=0, repo_path=str(repo)))


def test_an_unknown_new_holder_kind_is_refused_before_anything_is_stopped(
        repo, channel, monkeypatch):
    fake = FakeLeases(_held())
    monkeypatch.setattr(control, "leases", fake)
    with pytest.raises(control.ControlRefused):
        _run(control.preempt(subject_id="wt-1", new_holder_id="chat-9",
                             actor="test-operator", human_action="take over",
                             new_holder_kind="daemon", grace_sec=0,
                             repo_path=str(repo)))
    assert fake.calls == []
