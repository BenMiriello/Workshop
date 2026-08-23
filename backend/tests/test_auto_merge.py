"""Auto-merge lands a pre-approved change, and refuses everything else.

Step C7 of `docs/platform-redesign/changes-and-review-2026-08-22.md` sec 6.

WHAT THESE PIN. Not that a merge works -- `test_work_review.py` owns that -- but
that the DECISION to merge is made on evidence and made conservatively. The
failure this feature can have that matters is a merge that happened because a
check errored, was missing, or was an opinion rather than a fact, so most of
what follows is a catalogue of ways to be uncertain, each of which must end with
nothing merged.

No database and no git: `assess` is a function of a change record, and
`consider` reaches the store and the merge path through module attributes these
tests replace. The database-backed half of the change record lives in
test_changes.py.
"""
from __future__ import annotations

import json

import pytest

from services import auto_merge, change_gates, claim_check, merge_policy

CHANGE_ID = "11111111-1111-1111-1111-111111111111"
HEAD = "a" * 40


# --- fixtures ---------------------------------------------------------------

def _gates(overrides: dict | None = None, drop: tuple[str, ...] = (),
           extra: list[dict] | None = None) -> list[dict]:
    """A complete reviewer run: every gate `change_gates` produces, all passing,
    minus whatever a test wants missing and plus whatever it wants added."""
    over = overrides or {}
    out = [{"name": n, "status": over.get(n, "pass"),
            "detail": "checked", "at": "2026-08-23T00:00:00+00:00"}
           for n in change_gates.GATE_NAMES if n not in drop]
    return out + list(extra or [])


def _change(**over) -> dict:
    base = {
        "id": CHANGE_ID,
        "project_slug": "alpha",
        "branch_slug": "task-abc",
        "base_ref": "main",
        "head_sha": HEAD,
        "title": "A small change",
        "summary": "",
        "ticket_ids": ["abc"],
        "status": "ready",
        "gates": _gates(),
        "auto_merge": False,
        "merged_sha": None,
    }
    base.update(over)
    return base


class _Changes:
    """Stand-in for services.changes."""

    def __init__(self, change=None, diff=None, raise_on_get=None,
                 raise_on_diff=None):
        self._change = change
        self._diff = diff or {"summary": "", "files": []}
        self._raise_on_get = raise_on_get
        self._raise_on_diff = raise_on_diff

    def get_change(self, change_id):
        if self._raise_on_get:
            raise self._raise_on_get
        return dict(self._change)

    def diff_for(self, change_id):
        if self._raise_on_diff:
            raise self._raise_on_diff
        return self._diff


class _WorkReview:
    """Stand-in for services.work_review, recording the merge it was asked for."""

    MergeRefused = auto_merge.work_review.MergeRefused

    def __init__(self, result=None, refuse=""):
        self.result = result or {"merged": True, "commit": "deadbee",
                                 "reason": "", "change_id": CHANGE_ID}
        self.refuse = refuse
        self.calls: list[dict] = []

    def merge_change(self, change, *, message="", merged_by="operator", basis=""):
        self.calls.append({"change": change, "message": message,
                           "merged_by": merged_by, "basis": basis})
        if self.refuse:
            raise self.MergeRefused(self.refuse)
        return self.result


class _Worktrees:
    def __init__(self, state=None, raise_on_state=None):
        self._state = state or {"exists": True, "ahead": 1, "behind": 0,
                                "head_sha": HEAD, "base_ref": "main",
                                "branch": "wt/alpha/task-abc"}
        self._raise = raise_on_state

    def branch_state(self, project_slug, branch_slug):
        if self._raise:
            raise self._raise
        return dict(self._state)


class _BuildAgents:
    """Stand-in for the registry lookup `scope_for` starts from."""

    def __init__(self, root="/tmp/alpha"):
        self._root = root

    def _project_dir(self, slug):
        return self._root


class _TaskScope:
    def __init__(self, root="/tmp/alpha", key="project:alpha"):
        self._root = root
        self._key = key

    def root_for_context(self, context_key):
        return self._root

    def key_for_root(self, root):
        return self._key


class _Tasks:
    def __init__(self, task=None, violations=None):
        self._task = task
        self._violations = violations or []

    def get_task(self, root, tid):
        return None if self._task is None else dict(self._task)

    def touches_violations(self, root, tid, paths):
        return list(self._violations)


@pytest.fixture()
def policy_store(tmp_path, monkeypatch):
    """A throwaway merge-policy store. Every test starts with NO file, which is
    the shipped state, so a test that does not opt in is testing the default."""
    path = tmp_path / ".runtime" / "merge-policy.json"
    monkeypatch.setattr(merge_policy, "_store_path", lambda: path)
    return path


@pytest.fixture()
def wired(monkeypatch, policy_store):
    """Replace everything `auto_merge` reaches out to, and hand the test back
    the doubles so it can assert on what was asked of them."""
    def _wire(change=None, *, merge=None, worktrees=None, task_scope=None,
              tasks=None, build_agents=None, diff=None, raise_on_get=None,
              raise_on_diff=None):
        changes = _Changes(change=change or _change(), diff=diff,
                           raise_on_get=raise_on_get,
                           raise_on_diff=raise_on_diff)
        work_review = merge or _WorkReview()
        monkeypatch.setattr(auto_merge, "changes", changes)
        monkeypatch.setattr(auto_merge, "work_review", work_review)
        monkeypatch.setattr(auto_merge, "worktrees", worktrees or _Worktrees())
        monkeypatch.setattr(auto_merge, "task_scope", task_scope or _TaskScope())
        monkeypatch.setattr(auto_merge, "build_agents",
                            build_agents or _BuildAgents())
        monkeypatch.setattr(auto_merge, "tasks", tasks or _Tasks())
        return changes, work_review
    return _wire


# --- the happy path ---------------------------------------------------------

def test_a_green_pre_approved_change_merges(wired):
    _, merges = wired(_change(auto_merge=True))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES, claim_check.ADVISORY_GATES, trigger="test")

    assert out["merged"] is True
    assert out["commit"] == "deadbee"
    assert len(merges.calls) == 1, "it merges once, through the existing path"


def test_it_merges_through_the_existing_path_and_not_its_own(wired, monkeypatch):
    """There must be exactly one implementation of "pin the base, merge the
    branch, record it". This module may only call it."""
    _, merges = wired(_change(auto_merge=True))
    monkeypatch.setattr(auto_merge, "worktrees", _Worktrees())

    auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert merges.calls, "the merge went through work_review.merge_change"
    assert not hasattr(auto_merge, "merge_branch"), \
        "auto_merge must not grow a second merge implementation"


# --- the record distinguishes auto from human -------------------------------

def test_the_record_says_it_was_auto_merged_and_on_what_basis(wired):
    """An auto-merge indistinguishable from a human approval is a lie in the
    record. The actor names WHICH pre-approval, and the basis is the conditions
    that were true, so a person reading it months later can reconstruct it."""
    _, merges = wired(_change(auto_merge=True))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES, claim_check.ADVISORY_GATES, trigger="reviewer gates recorded")

    call = merges.calls[0]
    assert call["merged_by"] == "change_auto_merge"
    assert "reviewer gates recorded" in call["basis"], \
        "the basis names what asked for the merge"
    assert "gates_green" in call["basis"] and "pre_approved" in call["basis"], \
        "and every condition that had to hold for it"
    assert out["actor"] == "change_auto_merge"


def test_a_scope_policy_merge_is_recorded_as_the_scope_and_not_the_person(
        wired, policy_store):
    """The weaker claim, recorded as the weaker claim: nobody read THIS diff."""
    merge_policy.set_policy("project:alpha", "auto_when_green")
    _, merges = wired(_change())

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is True
    assert merges.calls[0]["merged_by"] == "scope_merge_policy"


def test_the_scope_key_is_the_one_the_operator_can_set(wired, policy_store):
    """The platform repo's registry slug is `development` and so is its scope
    key -- never `project:development`. If this module derived the key the other
    way round, a policy set on `development` would apply to nothing and the
    control would silently do nothing at all."""
    merge_policy.set_policy("development", "auto_when_green")
    _, merges = wired(_change(project_slug="development"),
                      task_scope=_TaskScope(root="/workspace", key="development"))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is True
    assert merges.calls[0]["merged_by"] == "scope_merge_policy"


def test_a_scope_that_does_not_resolve_has_no_policy(wired, policy_store):
    """The developer-mode interlock and the scope-owner check both live in
    `root_for_context`, and a key that will not round-trip through it is
    refused rather than used."""
    class _Gated(_TaskScope):
        def root_for_context(self, context_key):
            return None

    merge_policy.set_policy("project:alpha", "auto_when_green")
    _, merges = wired(_change(), task_scope=_Gated())

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


def test_an_unregistered_project_has_no_policy(wired, policy_store):
    class _NoProject(_BuildAgents):
        def _project_dir(self, slug):
            return None

    merge_policy.set_policy("project:alpha", "auto_when_green")
    _, merges = wired(_change(), build_agents=_NoProject())

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_a_change_flag_outranks_a_scope_policy_in_the_record(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green")
    _, merges = wired(_change(auto_merge=True))

    auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert merges.calls[0]["merged_by"] == "change_auto_merge", \
        "a person approved this diff; recording the weaker basis would understate it"


# --- the default configuration merges nothing -------------------------------

def test_the_default_configuration_merges_nothing(wired):
    """Green gates, a clean branch, and nobody armed it: nothing happens."""
    _, merges = wired(_change())

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "pre-approved" in out["reason"]
    assert merges.calls == []


def test_nothing_in_this_tree_arms_a_scope():
    """The boundary on this work, asserted rather than promised."""
    assert merge_policy.configured_scopes() == []


# --- one failing gate --------------------------------------------------------

def test_one_failing_gate_stops_the_merge(wired):
    _, merges = wired(_change(auto_merge=True,
                              gates=_gates({"design_system": "fail"})))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "design_system" in out["reason"]
    assert merges.calls == []


def test_a_running_gate_is_not_a_passing_one(wired):
    """`running` is a real GATE_STATUSES value: a check that started. Waiting
    for it is the only correct thing to do."""
    _, merges = wired(_change(auto_merge=True,
                              gates=_gates({"reviewer": "running"})))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


# --- advisory gates are neither a blocker nor an approval --------------------

def test_a_change_whose_only_passing_gates_are_advisory_does_not_merge(wired):
    """Every real check skipped, the claim-to-diff check green. That is a change
    nobody checked, and a judgement gate must not be able to stand in for one."""
    advisory = claim_check.GATE_NAME
    skipped = {n: "skipped" for n in change_gates.GATE_NAMES}
    gates = _gates(skipped, extra=[{"name": advisory, "status": "pass",
                                    "detail": "", "at": ""}])
    _, merges = wired(_change(auto_merge=True, gates=gates))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "advisory" in out["reason"]
    assert merges.calls == []


def test_a_failing_advisory_gate_does_not_hold_a_green_change_hostage(wired):
    """The other half of the same rule, and the reason C6 published the list: a
    judgement that can be wrong must not block code."""
    gates = _gates(extra=[{"name": claim_check.GATE_NAME, "status": "fail",
                           "detail": "a claim with nothing behind it",
                           "at": ""}])
    _, merges = wired(_change(auto_merge=True, gates=gates))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is True
    assert len(merges.calls) == 1


def test_the_advisory_list_is_read_and_not_hardcoded(wired, monkeypatch):
    """A second advisory gate, invented at runtime, must be excluded too --
    which is only true if this module reads `claim_check.ADVISORY_GATES`."""
    monkeypatch.setattr(claim_check, "ADVISORY_GATES",
                        (claim_check.GATE_NAME, "second_opinion"))
    gates = _gates(extra=[{"name": "second_opinion", "status": "fail",
                           "detail": "", "at": ""}])
    _, merges = wired(_change(auto_merge=True, gates=gates))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is True


# --- a missing gate is not a passing one ------------------------------------

def test_a_missing_gate_stops_the_merge(wired):
    """The check never ran. An absent gate reads to a person exactly like one
    that did not apply, which is why `change_gates` records skips -- and why
    "not recorded at all" has to mean the checking layer did not finish."""
    _, merges = wired(_change(auto_merge=True, gates=_gates(drop=("reviewer",))))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "reviewer" in out["reason"] and "never recorded" in out["reason"]
    assert merges.calls == []


def test_requiring_no_gates_at_all_does_not_merge(wired):
    """The vacuous-truth case one level up: "every required gate is present" is
    trivially true when nothing was required, and a caller that passed an empty
    set must not get a merge out of it."""
    _, merges = wired(_change(auto_merge=True))

    out = auto_merge.consider(CHANGE_ID, (), claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert merges.calls == []


def test_a_change_with_no_gates_at_all_does_not_merge(wired):
    _, merges = wired(_change(auto_merge=True, gates=[]))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


def test_one_lonely_green_gate_is_not_a_clean_bill_of_health(wired):
    """The vacuous-truth case: "every gate present passes" is trivially true of
    a change carrying one gate, and must not be enough."""
    _, merges = wired(_change(auto_merge=True, gates=[
        {"name": "reviewer", "status": "pass", "detail": "", "at": ""}]))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


# --- an unknown gate is not a passing one -----------------------------------

def test_an_unknown_gate_status_stops_the_merge(wired):
    """Written by something this build does not understand. "I do not
    understand it" is not "it is fine"."""
    _, merges = wired(_change(auto_merge=True,
                              gates=_gates({"typecheck:python": "probably_ok"})))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "does not recognise" in out["reason"]
    assert merges.calls == []


def test_a_malformed_gate_list_stops_the_merge(wired):
    _, merges = wired(_change(auto_merge=True, gates=["not a gate"]))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


# --- an unreadable policy ----------------------------------------------------

def test_an_unreadable_policy_does_not_merge(wired, policy_store):
    """The file that would have armed this scope is corrupt. The answer is the
    same as if it had never existed, and it is not "merge"."""
    policy_store.parent.mkdir(parents=True, exist_ok=True)
    policy_store.write_text("{ truncated")
    _, merges = wired(_change())

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert merges.calls == []


def test_a_policy_read_that_raises_does_not_merge(wired, monkeypatch):
    """Belt and braces: even if the policy module itself blew up, the condition
    it answers is treated as unmet rather than skipped."""
    def _boom(scope):
        raise RuntimeError("the policy store exploded")
    monkeypatch.setattr(merge_policy, "allows_auto_merge", _boom)
    _, merges = wired(_change())

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "not met" in out["reason"] or "pre-approved" in out["reason"]
    assert merges.calls == []


# --- an erroring classifier --------------------------------------------------

def test_a_classifier_that_errors_does_not_merge(wired, policy_store):
    """The single worst failure this feature can have, pinned: the low-risk
    classifier could not produce the diff it classifies, and that is a refusal
    rather than a shrug."""
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), raise_on_diff=RuntimeError("git is gone"))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "not met" in out["reason"]
    assert merges.calls == []


def test_a_condition_that_raises_anywhere_does_not_merge(wired):
    """`branch_state` throwing stands in for every unexpected failure."""
    _, merges = wired(_change(auto_merge=True),
                      worktrees=_Worktrees(raise_on_state=OSError("no repo")))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


def test_a_change_that_cannot_be_read_does_not_merge_and_does_not_raise(wired):
    _, merges = wired(raise_on_get=RuntimeError("no relational store"))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert merges.calls == []


# --- a conflict parks exactly as it does today -------------------------------

def test_a_conflict_parks_and_changes_nothing(wired):
    """`merge_change` raising MergeRefused is what a conflict has always looked
    like. Auto-merge does not retry, does not force, does not move the ticket,
    and does not mark the change merged -- it reports git's words and leaves the
    branch for a person, which is the park path this design already had."""
    refusing = _WorkReview(refuse="CONFLICT (content): Merge conflict in a.py")
    _, merges = wired(_change(auto_merge=True), merge=refusing)

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "CONFLICT" in out["reason"]
    assert out["actor"] is None, "nothing merged, so nobody merged it"
    assert len(merges.calls) == 1, "it tried exactly once"


def test_a_merge_path_that_explodes_is_reported_not_raised(wired):
    class _Exploding(_WorkReview):
        def merge_change(self, change, **kw):
            raise ValueError("something unexpected")

    _, merges = wired(_change(auto_merge=True), merge=_Exploding())

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


# --- the status and the branch ----------------------------------------------

@pytest.mark.parametrize("status", ["draft", "changes_requested", "merged",
                                    "closed"])
def test_only_a_finished_open_change_may_auto_merge(wired, status):
    _, merges = wired(_change(auto_merge=True, status=status))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


def test_a_branch_that_moved_since_the_gates_ran_does_not_merge(wired):
    """The gates judged a commit that is no longer the head."""
    moved = _Worktrees({"exists": True, "ahead": 2, "behind": 0,
                        "head_sha": "b" * 40, "base_ref": "main",
                        "branch": "wt/alpha/task-abc"})
    _, merges = wired(_change(auto_merge=True), worktrees=moved)

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "moved" in out["reason"]
    assert merges.calls == []


def test_a_branch_with_nothing_ahead_does_not_merge(wired):
    empty = _Worktrees({"exists": True, "ahead": 0, "behind": 0,
                        "head_sha": HEAD, "base_ref": "main",
                        "branch": "wt/alpha/task-abc"})
    _, merges = wired(_change(auto_merge=True), worktrees=empty)

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_a_change_that_names_no_branch_does_not_merge(wired):
    _, merges = wired(_change(auto_merge=True, branch_slug=""))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


# --- the low-risk classifier -------------------------------------------------

def _small_diff(path="backend/services/thing.py", status="A", body=None):
    patch = body if body is not None else "--- a\n+++ b\n+one line\n"
    return {"summary": "1 file changed", "files": [
        {"path": path, "status": status, "diff": patch}]}


def _ticket(touches=("backend/**",)):
    return {"id": "abc", "touches": list(touches)}


def test_a_small_in_bounds_change_is_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(), tasks=_Tasks(_ticket()))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is True
    assert merges.calls[0]["merged_by"] == "scope_merge_policy"


@pytest.mark.parametrize("path", [
    "backend/requirements.txt",
    "frontend/package.json",
    "backend/db/migrations/versions/abc_thing.py",
    "backend/deps_auth.py",
    "backend/services/secrets_store.py",
    "infra/.env.example",
])
def test_the_design_s_risk_signals_are_not_low_risk(wired, policy_store, path):
    """No dependency change, no migration, no secret or auth path -- the
    design's own list (sec 6), each one pinned."""
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(path), tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False
    assert merges.calls == []


def test_a_deletion_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(status="D"),
                      tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_too_many_files_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    files = [{"path": f"backend/f{i}.py", "status": "A", "diff": "+x\n"}
             for i in range(auto_merge.LOW_RISK_MAX_FILES + 1)]
    _, merges = wired(_change(), diff={"summary": "", "files": files},
                      tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_too_many_lines_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    body = "--- a\n+++ b\n" + "+line\n" * (auto_merge.LOW_RISK_MAX_CHANGED_LINES + 1)
    _, merges = wired(_change(), diff=_small_diff(body=body),
                      tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_an_empty_diff_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff={"summary": "", "files": []},
                      tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_a_ticket_that_declares_no_touches_is_not_low_risk(wired, policy_store):
    """The classifier's question is "can I prove this stayed inside a declared
    boundary". No boundary is not a yes -- which is the OPPOSITE of what
    `tasks._check_touches` answers, and deliberately so."""
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(),
                      tasks=_Tasks(_ticket(touches=())))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_a_ticket_that_does_not_resolve_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(), tasks=_Tasks(None))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_a_change_carrying_no_ticket_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(ticket_ids=[]), diff=_small_diff(),
                      tasks=_Tasks(_ticket()))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is False


def test_straying_outside_the_declared_touches_is_not_low_risk(wired, policy_store):
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    _, merges = wired(_change(), diff=_small_diff(),
                      tasks=_Tasks(_ticket(), violations=["frontend/x.tsx"]))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    assert out["merged"] is False
    assert "touches" in out["reason"]


def test_the_low_risk_classifier_is_skipped_for_a_per_change_approval(
        wired, policy_store):
    """A person read this diff. Re-deciding for them on a size heuristic would
    make the control they were given not work, so a big risky diff a human
    pre-approved still merges."""
    merge_policy.set_policy("project:alpha", "auto_when_green_and_low_risk")
    huge = {"summary": "", "files": [
        {"path": "backend/db/migrations/versions/x.py", "status": "D",
         "diff": "-line\n" * 500}]}
    _, merges = wired(_change(auto_merge=True), diff=huge, tasks=_Tasks(None))

    assert auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)["merged"] is True


# --- the assessment is readable ---------------------------------------------

def test_every_condition_is_named_in_the_record(wired):
    """"Explicit and readable, not implied": the refusal must say which
    condition, and the assessment must carry all of them."""
    _, _ = wired(_change(auto_merge=True, gates=_gates({"reviewer": "fail"})))

    out = auto_merge.consider(CHANGE_ID, change_gates.GATE_NAMES,
                                claim_check.ADVISORY_GATES)

    names = [c["name"] for c in out["assessment"]["conditions"]]
    assert "open_for_merge" in names
    assert "pre_approved" in names
    assert "gates_complete" in names
    assert "gates_green" in names
    assert json.dumps(out["assessment"]), "the assessment is serialisable"
