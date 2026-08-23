"""Auto-merge: a change that was pre-approved lands by itself when it is ready.

Step C7 of `docs/platform-redesign/changes-and-review-2026-08-22.md`, section 6.

THE STANDARD MECHANISM UNDER ITS STANDARD NAME. The operator asked for a
"pre-approve mechanic"; every system that has this calls it **auto-merge**
(GitLab renamed merge-when-pipeline-succeeds to Auto-merge in 16.0 and widened
it to merge-when-checks-pass in 16.9; GitHub has per-PR auto-merge). Approve
now, merge automatically when the checks pass. Two knobs, both standard, and
deliberately no more:

  * PER CHANGE  `change.auto_merge` -- a person read THIS diff and does not want
    to babysit the gates.
  * PER SCOPE   `services/merge_policy.py` -- `manual` (default),
    `auto_when_green`, or `auto_when_green_and_low_risk`.

IT DOES NOT MERGE ANYTHING ITSELF. The merge is `work_review.merge_change`,
which is the same call a human acceptance makes, which is `worktrees.merge_branch`
underneath. A conflict comes back from there exactly as it does today -- the
branch untouched, the change untouched, a reason in hand -- and this module's
answer is then simply "not merged, because <git's words>". That is the
park-on-conflict failure path, unchanged, and it is why there is no second merge
implementation here to drift from the first.

EVERY UNCERTAINTY IS A REFUSAL. An unknown gate status, a gate the reviewer
never wrote, a policy file that will not parse, a diff that cannot be produced,
a ticket that will not resolve, an exception anywhere in this module: each of
them answers "do not merge". A merge that happened because a check ERRORED is
the worst failure this feature can have -- worse than never merging -- because
it converts an outage in the checking layer into landed, unreviewed code. So
`assess` treats a condition it could not evaluate as a condition that failed,
and says which one in the record.

ADVISORY GATES ARE NEITHER A BLOCKER NOR AN APPROVAL. `claim_check.ADVISORY_GATES`
is published by C6 precisely so a judgement gate can be excluded BY NAME rather
than hardcoded here: a check that can be wrong must not be able to hold code
hostage on an opinion, and by exactly the same argument it must not be able to
count as one of the greens that let code land. They are dropped from the
completeness count and from the pass count both. That list arrives as an
ARGUMENT, like the required set and for the same reason below.

THE LOW-RISK THRESHOLD IS THE OPERATOR'S CALL, AND IT IS CURRENTLY SET AS TIGHT
AS IT USEFULLY CAN BE. `LOW_RISK_MAX_FILES` and `LOW_RISK_MAX_CHANGED_LINES`
below are policy, not physics. Section 8 of the design doc records an OPEN
question that belongs to the operator -- whether `auto_when_green_and_low_risk`
should exist at all in a single-operator system, since it is the one place in
the design where the system merges code nobody looked at. Nothing in this tree
turns it on. A fail-closed default is not an answer to that question; it is what
lets the question stay open safely, and if the operator answers "no" the whole
`auto_when_green_and_low_risk` branch can be deleted without touching per-change
auto-merge, which is the half that is decided.

AND IT DOES NOT KNOW WHAT A CHECKER IS. `assess` and `consider` take BOTH the
set of gate names a complete run produces and the set that are advisory as
ARGUMENTS, rather than importing `change_gates.GATE_NAMES` and
`claim_check.ADVISORY_GATES`. Two reasons, and the second is measured: the
checkers' tables are the checkers', in the same sense `services/changes.py`
says it does not know what a reviewer is; and importing them here made the gate
writer, the claim checker and this module a mutual triangle
(`change_gates -> auto_merge -> claim_check -> change_gates`), which
`scripts/check_module_layout.py` reports as a second strongly-connected
component. Both are REQUIRED positional arguments with no defaults, so a caller
that forgets one gets a TypeError rather than a merge assessed against no
requirements at all.

LAYERING. Above `changes`, `merge_policy` and `work_review`, and below nothing.
Its callers are `change_gates.record_reviewer_run` (the moment the checks
finish, which is what "merge when the checks pass" means) and
`routes/changes.py` (the moment a person arms a change that may already be
green); both hold the two lists and hand them in.
"""
from __future__ import annotations

import logging
from typing import Callable, NamedTuple

from db.models import GATE_STATUSES
from services import (build_agents, changes, merge_policy, task_scope, tasks,
                      work_review, worktrees)

log = logging.getLogger(__name__)

__all__ = ["Condition", "Assessment", "assess", "consider", "is_low_risk",
           "MERGEABLE_STATUSES", "LOW_RISK_MAX_FILES",
           "LOW_RISK_MAX_CHANGED_LINES"]


# A change may only auto-merge out of a status that MEANS "this is finished and
# open for review". `draft` is unfinished by its own admission and
# `changes_requested` is a reviewer having said no; neither is a thing to land
# behind the operator's back, whatever the gates say.
MERGEABLE_STATUSES = ("ready", "approved")

# --- the low-risk classifier's thresholds ------------------------------------
# THE OPERATOR'S CALL. Set to the most conservative values that still admit a
# genuinely small change: a handful of files and a change small enough that a
# person could read the whole thing in a minute if they chose to. Reviewer
# accuracy is measured to collapse as diff size grows (design doc sec 5.6,
# sec 9.3), which is the entire reason a size ceiling is one of the conditions.
LOW_RISK_MAX_FILES = 5
LOW_RISK_MAX_CHANGED_LINES = 100

# Git's name-status letters this classifier is willing to call low risk. `A` and
# `M` only: a deletion or a rename is a risk signal in its own right (design doc
# sec 5.6) and a letter this build does not know is, by the rule at the top of
# this module, not something to guess about.
_LOW_RISK_FILE_STATUSES = ("A", "M")

# Path fragments that make a change NOT low risk, by the design's own list: no
# dependency change, no migration, no secret or auth path. Matched against the
# lowercased posix path, substring-wise, because a manifest moved one directory
# down is the same manifest.
_DEPENDENCY_PATHS = (
    "requirements.txt", "requirements-", "package.json", "package-lock.json",
    "pyproject.toml", "poetry.lock", "pnpm-lock.yaml", "yarn.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile", "pipfile",
)
_MIGRATION_PATHS = ("/migrations/", "migrations/versions/", "alembic.ini",
                    "/schema.sql")
_SECRET_OR_AUTH_PATHS = (
    "auth", "secret", "credential", "password", "token", ".env", "ownership",
    "tenancy", "permission", "session_key", "middleware/",
)


class Condition(NamedTuple):
    """One thing that must hold before a change may merge itself.

    `ok` is the verdict and `detail` is the sentence a person reads afterwards
    when they ask why this landed, or why it did not. Both are recorded whether
    the condition held or not: the basis written onto the merged row is built
    out of these, and a basis that only lists what passed would be an argument
    rather than a record.
    """
    name: str
    ok: bool
    detail: str


class Assessment(NamedTuple):
    merge: bool
    actor: str | None
    basis: str
    refusal: str
    conditions: tuple[Condition, ...]

    def as_dict(self) -> dict:
        return {
            "merge": self.merge,
            "actor": self.actor,
            "basis": self.basis,
            "refusal": self.refusal,
            "conditions": [c._asdict() for c in self.conditions],
        }


def _safely(name: str, fn: Callable[[], Condition]) -> Condition:
    """Evaluate one condition, turning ANY exception into a failed condition.

    This is the module's central promise made mechanical. A condition that
    raised is a condition nobody answered, and an unanswered condition is not a
    satisfied one.
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - see the module docstring
        log.warning("auto_merge: condition %s could not be evaluated: %s",
                    name, e)
        return Condition(name, False,
                         f"could not be evaluated ({type(e).__name__}: {e}); "
                         f"treated as not met")


# --- the individual conditions ------------------------------------------------

def _open_for_merge(change: dict) -> Condition:
    status = str(change.get("status") or "")
    if status in MERGEABLE_STATUSES:
        return Condition("open_for_merge", True,
                         f"the change is {status}")
    return Condition("open_for_merge", False,
                     f"the change is {status or 'in no status'}, and only "
                     f"{' or '.join(MERGEABLE_STATUSES)} may auto-merge")


def _names_a_branch(change: dict) -> Condition:
    if change.get("project_slug") and change.get("branch_slug"):
        return Condition("names_a_branch", True,
                         f"{change['project_slug']}/{change['branch_slug']}")
    return Condition("names_a_branch", False,
                     "the change does not name a project and branch")


def scope_for(change: dict) -> str | None:
    """The scope key whose `merge_policy` governs this change, or None.

    A change is work on a project tree, and a scope IS a tree
    (`services/task_scope.py`), so the mapping is the project's root read back
    as a scope key -- the same inversion `services/makers.py::scope_for` makes
    for a maker, through the same two calls, deliberately.

    IT MUST PRODUCE THE KEY THE OPERATOR SET, or the control lies. The platform
    repo's registry slug is `development` and its scope key is ALSO
    `development`, never `project:development` (task_scope sec 4, "ONE NAME") --
    so asking `root_for_context("project:<slug>")` first would answer None for
    the one scope most likely to be configured, and a policy set through
    `POST /agents/autonomy/merge-policy` would sit there doing nothing. Going
    root-first and inverting gets the same key from both directions.

    THE GATE STILL APPLIES. The key is round-tripped back through
    `root_for_context`, which is where the developer-mode interlock and the
    scope-owner check live; a key that does not resolve there is refused rather
    than used. None means NO POLICY APPLIES, and the change can then only merge
    on its own `auto_merge` flag -- a person's decision about a specific diff,
    which needs no scope.
    """
    slug = str(change.get("project_slug") or "").strip()
    if not slug:
        return None
    root = build_agents._project_dir(slug)
    if root is None:
        return None
    key = task_scope.key_for_root(root)
    if not key or task_scope.root_for_context(key) is None:
        return None
    return key


def _pre_approved(change: dict) -> Condition:
    """Somebody armed this, and the record says WHICH somebody.

    The per-change flag wins when both are set, and that is not arbitrary: it is
    the stronger claim. `auto_merge` on the change means a person read this diff
    and pre-approved it; a scope policy means nobody looked at this diff in
    particular. Recording the weaker of the two would overstate what happened,
    and recording the stronger when only the weaker is true would be a lie.
    """
    if change.get("auto_merge"):
        return Condition("pre_approved", True,
                         "the change carries auto-merge: a person approved "
                         "this diff and asked for it to land when green")
    scope = scope_for(change)
    if scope and merge_policy.allows_auto_merge(scope):
        return Condition(
            "pre_approved", True,
            f"scope {scope} has merge_policy={merge_policy.get_policy(scope)}")
    where = f"scope {scope}" if scope else "no scope resolves for this change"
    return Condition("pre_approved", False,
                     f"nobody pre-approved this change: auto_merge is off and "
                     f"{where} ({merge_policy.DEFAULT_POLICY} by default)")


def _actor_for(change: dict) -> str:
    """Which of `db.models.MERGE_ACTORS` is about to merge. Mirrors the
    precedence `_pre_approved` documents."""
    if change.get("auto_merge"):
        return "change_auto_merge"
    return "scope_merge_policy"


def _advisory(advisory_gates: tuple[str, ...]) -> frozenset[str]:
    """The gate names that are findings rather than preconditions.

    Handed in (from `claim_check.ADVISORY_GATES`) and never spelled out here:
    C6 publishes that list for exactly this, and a second copy would be the
    copy that goes stale the day a second advisory check is written.
    """
    return frozenset(str(n) for n in (advisory_gates or ()) if str(n).strip())


def _gates_complete(change: dict, required: tuple[str, ...],
                    advisory: frozenset[str]) -> Condition:
    """Every check a full reviewer run produces is present on the record.

    ABSENT IS NOT GREEN. `change_gates` already refuses to omit a check that did
    not apply, precisely so a reviewer can tell "did not apply" from "never
    ran"; this condition is that distinction made load-bearing. Without it, a
    change carrying one green gate and nineteen missing ones would satisfy
    "every gate passes" vacuously, and a checking layer that fell over would
    read as a clean bill of health.

    AN EMPTY REQUIREMENT IS A REFUSAL, not a pass. "Every required gate is
    present" is trivially true of a caller that required nothing, and a
    vacuous truth is exactly the shape of failure this whole condition exists
    to close.
    """
    wanted = tuple(n for n in (required or ())
                   if str(n).strip() and n not in advisory)
    if not wanted:
        return Condition("gates_complete", False,
                         "no gate set was required of this change, so there is "
                         "nothing that being green could mean")
    present = {str(g.get("name") or "") for g in (change.get("gates") or [])
               if isinstance(g, dict)}
    missing = [n for n in wanted if n not in present]
    if missing:
        shown = ", ".join(missing[:6]) + (f" (+{len(missing) - 6} more)"
                                          if len(missing) > 6 else "")
        return Condition("gates_complete", False,
                         f"{len(missing)} gate(s) were never recorded on this "
                         f"change: {shown}")
    return Condition("gates_complete", True,
                     f"all {len(wanted)} required gates are recorded")


def _gates_green(change: dict, advisory: frozenset[str]) -> Condition:
    """Every non-advisory gate passed or did not apply, and at least one passed.

    Three rules, and each is here because its absence is a way to merge on
    nothing:

      * `fail` blocks. Obviously. So does `running`: a check that has started
        and not finished has not answered, and "merge when the checks pass" is
        not "merge while they are still going".
      * A status this build does not know blocks. `GATE_STATUSES` is the
        vocabulary; anything else is a record written by something this code
        does not understand, and "I do not understand it" is not "it is fine".
      * `skipped` does NOT block -- most reviewer checks legitimately do not
        apply to a given diff -- but it does not COUNT either, so a change whose
        every applicable check was skipped has zero passes and does not merge.
        The same rule is what stops an advisory-only change from qualifying,
        since advisory gates are dropped before any of this.
    """
    considered = [g for g in (change.get("gates") or [])
                  if isinstance(g, dict) and g.get("name") not in advisory]
    failed = [g for g in considered if g.get("status") == "fail"]
    pending = [g for g in considered if g.get("status") == "running"]
    unknown = [g for g in considered if g.get("status") not in GATE_STATUSES]
    passed = [g for g in considered if g.get("status") == "pass"]
    if failed:
        names = ", ".join(str(g.get("name")) for g in failed[:6])
        return Condition("gates_green", False,
                         f"{len(failed)} gate(s) failed: {names}")
    if pending:
        names = ", ".join(str(g.get("name")) for g in pending[:6])
        return Condition("gates_green", False,
                         f"{len(pending)} gate(s) are still running: {names}")
    if unknown:
        names = ", ".join(f"{g.get('name')}={g.get('status')!r}"
                          for g in unknown[:6])
        return Condition("gates_green", False,
                         f"{len(unknown)} gate(s) carry a status this build "
                         f"does not recognise: {names}")
    if not passed:
        return Condition("gates_green", False,
                         "no non-advisory gate passed; a change whose only "
                         "green gates are advisory has not been checked")
    skipped = len(considered) - len(passed)
    return Condition("gates_green", True,
                     f"{len(passed)} non-advisory gate(s) passed, {skipped} did "
                     f"not apply, none failed")


def _branch_ready(change: dict) -> Condition:
    """The branch still exists, still has something to merge, and has not moved
    since the record was brought in step with it.

    THE FRESHNESS CONDITION, and its limit is worth stating plainly: a gate
    entry is `{name, status, detail, at}` and carries no sha, because the
    design fixes that shape. So this cannot prove the gates judged THIS commit.
    What it can prove is that the branch has not moved since the change record
    last read it, which combined with the primary trigger -- this module is
    called by the gate writer, immediately after a run -- is the closest tie
    available without changing the gates column. A branch that has moved is a
    refusal: re-run the checks and it will be considered again.
    """
    state = worktrees.branch_state(change["project_slug"], change["branch_slug"])
    if not state.get("exists"):
        return Condition("branch_ready", False,
                         f"{state.get('branch')} does not resolve any more")
    if int(state.get("ahead") or 0) <= 0:
        return Condition("branch_ready", False,
                         f"{state.get('branch')} has nothing ahead of "
                         f"{state.get('base_ref')}")
    if str(state.get("head_sha") or "") != str(change.get("head_sha") or ""):
        return Condition("branch_ready", False,
                         "the branch has moved since this change was last "
                         "refreshed, so the recorded gates judged other code")
    return Condition("branch_ready", True,
                     f"{state.get('branch')} is {state.get('ahead')} commit(s) "
                     f"ahead of {state.get('base_ref')} at "
                     f"{str(state.get('head_sha'))[:12]}")


# --- the low-risk classifier --------------------------------------------------

def _changed_lines(patch: str) -> int:
    n = 0
    for line in (patch or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n


def _flagged(path: str) -> str | None:
    """Why this path is not low risk, or None. Design doc sec 6: no dependency
    change, no migration, no secret or auth path."""
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if any(f in name for f in _DEPENDENCY_PATHS):
        return "a dependency manifest"
    if any(f in p for f in _MIGRATION_PATHS):
        return "a schema migration"
    if any(f in p for f in _SECRET_OR_AUTH_PATHS):
        return "a secret or auth path"
    return None


def _touches_clean(change: dict, paths: list[str]) -> str | None:
    """Why the diff is not inside the tickets' declared `touches`, or None.

    FAIL CLOSED, WHICH IS THE OPPOSITE OF `tasks._check_touches`. That helper
    answers "may this task write here" and is deliberately permissive: an
    unknown ticket or an undeclared `touches` means unrestricted, because it
    exists to hold a ticket to a boundary IT stated, not to become a second
    silent way for work to fail. Here the question is the other one -- "can I
    prove this diff stayed inside a boundary" -- and an absent boundary means
    the answer is no. So: no tickets, a ticket that does not resolve, or a
    ticket declaring no `touches` all disqualify.
    """
    ticket_ids = [str(t) for t in (change.get("ticket_ids") or []) if str(t)]
    if not ticket_ids:
        return "the change carries no ticket, so there is no declared boundary"
    scope = scope_for(change)
    root = task_scope.root_for_context(scope) if scope else None
    if root is None:
        return "the change's scope does not resolve, so `touches` is unreadable"
    for tid in ticket_ids:
        task = tasks.get_task(root, tid)
        if task is None:
            return f"ticket {tid} does not resolve in {scope}"
        if not [g for g in (task.get("touches") or []) if str(g).strip()]:
            return f"ticket {tid} declares no `touches` to stay inside"
        stray = tasks.touches_violations(root, tid, paths)
        if stray:
            return (f"{len(stray)} path(s) outside ticket {tid}'s declared "
                    f"touches: {', '.join(stray[:5])}")
    return None


def is_low_risk(change: dict) -> Condition:
    """The design's low-risk definition, all of which must hold (sec 6).

    Every gate green is checked separately (`_gates_green`) because it is a
    precondition for BOTH policies. What is left is this: the diff stays inside
    the ticket's declared `touches`; no dependency change, no migration, no
    secret or auth path; and a size ceiling. Anything this cannot classify is
    not low risk -- the Ona result the design cites (auto-approving low-risk PRs
    cut lead time 74%) depends entirely on the classifier being conservative,
    and a classifier that guesses is not conservative, it is fast.
    """
    payload = changes.diff_for(change["id"])
    files = list(payload.get("files") or [])
    if not files:
        return Condition("low_risk", False,
                         "the diff is empty, which is not a change to classify")
    if len(files) > LOW_RISK_MAX_FILES:
        return Condition("low_risk", False,
                         f"{len(files)} files changed, over the ceiling of "
                         f"{LOW_RISK_MAX_FILES}")
    total = 0
    for f in files:
        path = str(f.get("path") or "")
        status = str(f.get("status") or "")
        if status not in _LOW_RISK_FILE_STATUSES:
            kind = status or "an unrecorded change kind"
            return Condition("low_risk", False,
                             f"{path} is {kind}, and only an added or modified "
                             f"file is classifiable as low risk")
        why = _flagged(path)
        if why:
            return Condition("low_risk", False, f"{path} is {why}")
        total += _changed_lines(str(f.get("diff") or ""))
    if total > LOW_RISK_MAX_CHANGED_LINES:
        return Condition("low_risk", False,
                         f"{total} changed lines, over the ceiling of "
                         f"{LOW_RISK_MAX_CHANGED_LINES}")
    stray = _touches_clean(change, [str(f.get("path") or "") for f in files])
    if stray:
        return Condition("low_risk", False, stray)
    return Condition("low_risk", True,
                     f"{len(files)} file(s), {total} changed line(s), inside "
                     f"the ticket's declared touches, no dependency, migration, "
                     f"secret or auth path")


# --- the assessment -----------------------------------------------------------

def assess(change: dict, required_gates: tuple[str, ...],
           advisory_gates: tuple[str, ...]) -> Assessment:
    """Every condition, evaluated, in the order a person would ask them.

    `required_gates` is the set of gate names a complete run of the checkers
    produces (`change_gates.GATE_NAMES`) and `advisory_gates` is the subset
    that are findings rather than preconditions (`claim_check.ADVISORY_GATES`).
    Both are passed in rather than imported; see the module docstring.

    Returns an `Assessment` and NEVER raises: a condition that blew up is a
    condition that failed (`_safely`), and a failure anywhere means no merge.
    The conditions are all evaluated rather than short-circuited on the first
    refusal, up to the point where evaluating one would be meaningless -- so the
    record shows what else was true, which is what makes it readable later.
    """
    advisory = _advisory(advisory_gates)
    conditions: list[Condition] = [
        _safely("open_for_merge", lambda: _open_for_merge(change)),
        _safely("names_a_branch", lambda: _names_a_branch(change)),
        _safely("pre_approved", lambda: _pre_approved(change)),
        _safely("gates_complete",
                lambda: _gates_complete(change, required_gates, advisory)),
        _safely("gates_green", lambda: _gates_green(change, advisory)),
    ]
    if all(c.ok for c in conditions):
        conditions.append(_safely("branch_ready", lambda: _branch_ready(change)))

    actor = _actor_for(change)
    # The low-risk classifier runs ONLY where the policy demands it: a per-change
    # pre-approval is a person saying they read this diff, and re-deciding for
    # them on a size heuristic would make the control they were given not work.
    if all(c.ok for c in conditions) and actor == "scope_merge_policy":
        scope = scope_for(change)
        if scope and merge_policy.requires_low_risk(scope):
            conditions.append(_safely("low_risk", lambda: is_low_risk(change)))

    failed = [c for c in conditions if not c.ok]
    if failed:
        return Assessment(False, None, "", failed[0].detail, tuple(conditions))
    basis = "; ".join(f"{c.name}: {c.detail}" for c in conditions)
    return Assessment(True, actor, basis, "", tuple(conditions))


def consider(change_id: str, required_gates: tuple[str, ...],
             advisory_gates: tuple[str, ...], *, trigger: str = "") -> dict:
    """Assess this change and merge it if every condition holds.

    The one entry point. `required_gates` is what a complete run of the
    checkers produces (`change_gates.GATE_NAMES`) and `advisory_gates` is which
    of them are only opinions (`claim_check.ADVISORY_GATES`); both are supplied
    by the caller for the reason the module docstring gives. Returns
    `{"merged", "reason", "commit", "change_id", "actor", "assessment"}` and
    NEVER RAISES -- its callers are a gate writer whose contract is that
    recording a verdict cannot change it, and an HTTP handler that has already
    succeeded at what it was asked to do.

    `trigger` is free text naming what asked (the gate run, the operator arming
    the change). It goes into the log and into the merge commit message, so
    `git log` on the trunk answers "what put this here" without a database.
    """
    try:
        change = changes.get_change(change_id)
    except Exception as e:  # noqa: BLE001
        log.warning("auto_merge: cannot read change %s: %s", change_id, e)
        return {"merged": False, "reason": f"the change could not be read: {e}",
                "commit": "", "change_id": str(change_id), "actor": None,
                "assessment": None}

    verdict = assess(change, required_gates, advisory_gates)
    if not verdict.merge:
        log.info("auto_merge: change %s not merged (%s)", change_id,
                 verdict.refusal)
        return {"merged": False, "reason": verdict.refusal, "commit": "",
                "change_id": str(change_id), "actor": None,
                "assessment": verdict.as_dict()}

    basis = verdict.basis if not trigger else f"trigger {trigger}; {verdict.basis}"
    message = (f"Merge change {change.get('title') or change_id} "
               f"(auto-merge, {verdict.actor})")
    try:
        result = work_review.merge_change(
            change, message=message, merged_by=verdict.actor, basis=basis)
    except work_review.MergeRefused as e:
        # THE CONFLICT PATH, and it is the existing one: the branch and the
        # change are exactly as they were, and a person now has to look at it.
        log.info("auto_merge: change %s could not merge: %s", change_id, e)
        return {"merged": False, "reason": str(e), "commit": "",
                "change_id": str(change_id), "actor": None,
                "assessment": verdict.as_dict()}
    except Exception as e:  # noqa: BLE001
        log.error("auto_merge: change %s: the merge path raised: %s",
                  change_id, e)
        return {"merged": False, "reason": f"the merge path failed: {e}",
                "commit": "", "change_id": str(change_id), "actor": None,
                "assessment": verdict.as_dict()}

    log.info("auto_merge: change %s merged as %s by %s", change_id,
             result.get("commit"), verdict.actor)
    return {"merged": bool(result.get("merged")),
            "reason": str(result.get("reason") or ""),
            "commit": str(result.get("commit") or ""),
            "change_id": str(change_id), "actor": verdict.actor,
            "assessment": verdict.as_dict()}
