"""Machine-gate results, routed onto the change they judged.

Step C3 of `docs/platform-redesign/changes-and-review-2026-08-22.md`. Section
5.3 states the rule this module implements: **machine gates first, human
attention last.** The repo already had the machine layer -- `reviewer.sh` and
everything it drives (the design lint, both typecheckers, the ratchets, the
route prober, the executed acceptance criteria) -- and it already produced a
per-check verdict for every one of them. What it did NOT do was put those
verdicts anywhere a reviewer could see: they went to `.runtime/reviewer.log`
and nowhere else, so the review surface said, honestly, that no machine checks
were recorded. A person then re-answered by hand questions a checker had
already answered.

THIS MODULE ONLY ROUTES. It does not run a check, add one, or change what any
existing one decides. `reviewer.sh` is still the sole author of every verdict
here; `gates_from_reviewer` translates its JSON into the `{name, status,
detail, at}` shape `db/models.Change.gates` already declares and
`services/changes.update_change` already validates.

ABSENT AND SKIPPED MUST NOT READ THE SAME. Most of the reviewer's checks are
gated on whether the diff touches the paths they inspect, so on any given run
half of them do not run at all. If those were simply left out, a reviewer
reading the surface could not tell "this check does not apply to this diff"
from "nobody ever ran this" -- and would either trust a pass that was never
made, or redo work that was. So every check in the table below is recorded on
every run, and a skip carries its REASON: which precondition was not met, or,
when the reviewer flagged the check as ambiguous, that it had input to judge
and no tooling to judge it with. That distinction is the reviewer's own
(`reviewer.sh`'s LATITUDE note) and it survives into the record here.

A GATE WRITE MAY NEVER FAIL THE SWEEP. The verdict is the reviewer's and it is
already made by the time this module is reached; recording it is bookkeeping.
No database, no open change for the branch, an unreadable payload -- each is
logged and returns None. `record_reviewer_run` raises nothing, which is why
`reviewer.sh` can call it without guarding the result and still keep its
"exits 0 always" contract.

AND IT PINS THE OWNER FIRST. `ownership.pin_operator()` before any store call.
The sweep helper that opens a change (`project_ralph.sh:open_change_for_task`)
was the sixth entrypoint to learn this the hard way -- without it the call dies
on `OwnerUnset` and the write silently never happens, which for a gate would
mean a change that looks unchecked because the bookkeeping crashed. The design
doc records that failure in its section 8; this is not a place to repeat it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, NamedTuple

from services import ownership

__all__ = ["gates_from_reviewer", "record_reviewer_run", "merge_gates",
           "GATE_CHECKS"]


class _Check(NamedTuple):
    """One reviewer check, and everything needed to record its outcome.

    `key` is the dotted path into `reviewer.sh`'s JSON. `describes` is what a
    pass MEANS, which is the detail a reviewer needs in order to skip the
    question. `skipped_because` is the precondition that was not met -- see
    the module docstring on why a skip is recorded rather than omitted.
    `ambiguity` are the tokens `reviewer.sh` puts in its `ambiguous` list when
    this check had real input and no tooling to judge it; that is a different
    skip from "did not apply" and says so.
    """
    gate: str
    key: str
    describes: str
    skipped_because: str
    ambiguity: tuple[str, ...] = ()


# The reviewer's checks, in the order it runs them. Every entry produces a gate
# on every run. Adding to this table is how a NEW reviewer check becomes
# visible on a change -- it is not where a new check is invented.
GATE_CHECKS: tuple[_Check, ...] = (
    _Check("typecheck:python", "typecheck.py",
           "mypy (or py_compile) is clean on the changed Python files",
           "no changed .py file to typecheck",
           ("py_typecheck_unavailable",)),
    _Check("typecheck:typescript", "typecheck.ts",
           "tsc --noEmit is clean across the frontend project and the ui kit",
           "no changed .ts/.tsx file under frontend/ or packages/ui/",
           ("ts_typecheck_unavailable",)),
    _Check("design_system", "design",
           "no new inline hex, emoji or not-allowed on added .tsx lines",
           "no changed .tsx under frontend/src or packages/ui/src",
           ("design_check_unavailable",)),
    _Check("pluggable_apps", "pluggable_apps",
           "no app welded back into a platform-core file, no per-instance "
           "data tracked",
           "the diff touches none of the files this check inspects",
           ("pluggable_apps_check_unavailable",)),
    _Check("hardcoded_identity", "hardcoding",
           "no hardcoded machine path, hostname or operator identity",
           "the checker is not present in this tree",
           ("hardcoding_check_unavailable",)),
    _Check("one_definition", "one_definition",
           "no enum value-set this diff touches is declared twice",
           "no changed backend .py or frontend/packages-ui .ts/.tsx file",
           ("one_definition_check_unavailable", "one_definition_check_errored")),
    _Check("ratchets_not_loosened", "ratchets_not_loosened",
           "no pinned ratchet baseline was rewritten to pass",
           "the diff touches no scripts/*baseline*.json",
           ("ratchets_not_loosened_check_unavailable",)),
    _Check("module_layout", "module_layout",
           "function-local imports, import cycles, upward layering and "
           "big-file count are no worse than the pinned baseline",
           "no changed backend .py file",
           ("module_layout_check_unavailable",)),
    _Check("no_process_globals", "no_process_globals",
           "no new module-level mutable global in backend/",
           "no changed backend .py file",
           ("no_process_globals_check_unavailable",)),
    _Check("no_state_in_source", "no_state_in_source",
           "no new mutable state file committed under a source directory",
           "the diff adds no .json/.db under backend, frontend or packages",
           ("no_state_in_source_check_unavailable",)),
    _Check("no_raw_paths", "no_raw_paths",
           "no new raw OS path in agent-facing text",
           "the diff touches none of the agent-facing surfaces this check scans",
           ("no_raw_paths_check_unavailable",)),
    _Check("no_app_names", "no_app_names",
           "no platform source file names a specific app",
           "the checker or its baseline is not present in this tree",
           ("no_app_names_check_unavailable",)),
    _Check("response_model", "response_model",
           "every route in a touched routes/ file declares response_model=",
           "the diff touches no backend/routes/*.py",
           ("response_model_check_unavailable",)),
    _Check("opaque_fields", "opaque_fields",
           "no response model gained an untyped field (dict[str, Any], "
           "list[dict], bare Any)",
           "the diff touches no backend source or the openapi generator",
           ("opaque_fields_check_unavailable",)),
    _Check("routes_answer", "routes_answer",
           "every GET route the running backend serves answers without a 5xx",
           "the diff touches no backend source or the openapi generator",
           ("routes_answer_check_unavailable", "routes_answer_check_could_not_run")),
    _Check("no_raw_storage", "no_raw_storage",
           "no localStorage/sessionStorage call outside lib/storage.ts",
           "no changed .ts/.tsx under frontend/src or mobile",
           ("no_raw_storage_check_unavailable",)),
    _Check("generated_client", "generated_client",
           "the committed api client still matches the schema the backend serves",
           "the diff touches no backend source, the generator or the api client",
           ("generated_client_check_unavailable",)),
    _Check("duplicate_helpers", "duplicate_helpers",
           "no new copy-pasted helper body",
           "no changed .ts/.tsx under frontend/src or packages/ui/src",
           ("duplicate_helpers_check_unavailable",)),
)

# reviewer.sh's own vocabulary for a check outcome -> the `gates` vocabulary
# (`db/models.GATE_STATUSES`). `skip` and `skipped` are both emitted: the
# hardcoding check defaults to the former, everything else to the latter.
_STATUS = {"ok": "pass", "pass": "pass", "fail": "fail",
           "skip": "skipped", "skipped": "skipped"}


def _dig(payload: dict, dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _reason_for(reasons: list[str], gate: str) -> str | None:
    """The reviewer's own words for why this gate failed, when it named them.

    `reasons` is a flat list of tokens (`design_violations`,
    `duplicate_definitions:3`) and the mapping from token to check is not
    one-to-one, so this matches on the gate's distinctive stem rather than
    guessing. A miss is fine: the check's own description is the fallback and
    is never wrong, only less specific.
    """
    stems = {
        "typecheck:python": ("py_typecheck",),
        "typecheck:typescript": ("ts_typecheck",),
        "design_system": ("design_violations",),
        "pluggable_apps": ("pluggable_app_violations",),
        "hardcoded_identity": ("hardcoded_identity",),
        "one_definition": ("duplicate_definitions",),
        "ratchets_not_loosened": ("ratchet_baseline_loosened",),
        "module_layout": ("module_layout_regression",),
        "no_process_globals": ("process_globals_regression",),
        "no_state_in_source": ("state_file_in_source",),
        "no_raw_paths": ("raw_path_in_agent_text",),
        "no_app_names": ("app_name_in_platform_source",),
        "response_model": ("response_model_regression",),
        "opaque_fields": ("opaque_response_field",),
        "routes_answer": ("route_returns_5xx",),
        "no_raw_storage": ("raw_browser_storage",),
        "generated_client": ("generated_client_stale",),
        "duplicate_helpers": ("duplicate_helper",),
    }.get(gate, ())
    for reason in reasons:
        if any(str(reason).startswith(stem) for stem in stems):
            return str(reason)
    return None


def _stub_gate(payload: dict, at: str) -> dict:
    """New stub markers, which the reviewer reports as hits rather than a
    status. Zero hits is a pass; an empty diff never got scanned at all and
    says so rather than banking a pass it did not earn."""
    hits = payload.get("stub_hits") or []
    if not isinstance(hits, list):
        hits = []
    if int(payload.get("diff_files") or 0) == 0 and not hits:
        return {"name": "no_new_stubs", "status": "skipped", "at": at,
                "detail": "nothing changed, so no file was scanned for stubs"}
    if hits:
        shown = "; ".join(
            f"{h.get('file')}:{h.get('line')} {h.get('marker')}"
            for h in hits[:5] if isinstance(h, dict))
        more = "" if len(hits) <= 5 else f" (+{len(hits) - 5} more)"
        return {"name": "no_new_stubs", "status": "fail", "at": at,
                "detail": f"{len(hits)} new stub marker(s): {shown}{more}"}
    return {"name": "no_new_stubs", "status": "pass", "at": at,
            "detail": "no TODO/FIXME/NotImplementedError/placeholder was "
                      "introduced in the changed files"}


def _criteria_gate(payload: dict, at: str) -> dict:
    """The ticket's own machine-checkable acceptance assertions, EXECUTED by
    `project_ralph.sh` and handed to the reviewer. A ticket that declared none
    records a skip, because "this ticket asserted nothing checkable" is a fact
    a reviewer wants and an absent gate would hide."""
    results = payload.get("criteria") or []
    if not isinstance(results, list) or not results:
        return {"name": "acceptance_criteria", "status": "skipped", "at": at,
                "detail": "the ticket declared no machine-checkable "
                          "acceptance criteria"}
    failed = [c for c in results if isinstance(c, dict) and not c.get("pass")]
    passed = len(results) - len(failed)
    if failed:
        first = str(failed[0].get("criterion") or "criterion")[:160]
        return {"name": "acceptance_criteria", "status": "fail", "at": at,
                "detail": f"{passed}/{len(results)} passed; first failure: {first}"}
    return {"name": "acceptance_criteria", "status": "pass", "at": at,
            "detail": f"{passed}/{len(results)} declared criteria passed, executed"}


def _verdict_gate(payload: dict, at: str) -> dict:
    """The reviewer's overall verdict, recorded as its own gate.

    `block-for-decision` is not a pass and is not a plain failure either: it is
    the reviewer saying completeness is UNESTABLISHED under a strict latitude.
    It records `fail` -- the change did not clear the gate -- with the
    reviewer's verbatim `blocked_reason`, so the surface never shows it as
    green and never loses why.
    """
    verdict = str(payload.get("verdict") or "").strip() or "unknown"
    reasons = [str(r) for r in (payload.get("reasons") or [])]
    latitude = str(payload.get("latitude") or "standard")
    if verdict == "approve" and payload.get("complete"):
        detail = f"approved at latitude {latitude}"
    elif verdict == "block-for-decision":
        detail = str(payload.get("blocked_reason")
                     or "completeness unestablished under a strict latitude")
    else:
        detail = (f"verdict {verdict} at latitude {latitude}"
                  + (f": {', '.join(reasons)}" if reasons else ""))
    status = "pass" if (verdict == "approve" and payload.get("complete")) else "fail"
    return {"name": "reviewer", "status": status, "at": at, "detail": detail[:600]}


def gates_from_reviewer(payload: dict) -> list[dict]:
    """`reviewer.sh`'s JSON -> the `gates` shape, one entry per check.

    Every check in `GATE_CHECKS` appears, whatever it did, for the reason the
    module docstring gives: an absent gate and a skipped one read identically
    to a person, and only one of them is true.
    """
    if not isinstance(payload, dict):
        raise ValueError("the reviewer payload must be a JSON object")
    at = str(payload.get("ts") or "").strip() or datetime.now(
        timezone.utc).isoformat(timespec="seconds")
    reasons = [str(r) for r in (payload.get("reasons") or [])]
    ambiguous = {str(a) for a in (payload.get("ambiguous") or [])}

    gates: list[dict] = [_verdict_gate(payload, at), _stub_gate(payload, at)]
    for check in GATE_CHECKS:
        raw = str(_dig(payload, check.key) or "").strip().lower()
        status = _STATUS.get(raw)
        if status is None:
            gates.append({
                "name": check.gate, "status": "skipped", "at": at,
                "detail": ("the reviewer reported no outcome for this check"
                           if not raw else
                           f"the reviewer reported an unrecognised outcome "
                           f"{raw!r}")})
            continue
        if status == "pass":
            detail = check.describes
        elif status == "fail":
            detail = _reason_for(reasons, check.gate) or f"failed: {check.describes}"
        else:
            hit = [tok for tok in check.ambiguity if tok in ambiguous]
            detail = (f"could not run ({hit[0]}): it had input to judge and no "
                      f"tooling to judge it with" if hit
                      else f"did not apply: {check.skipped_because}")
        gates.append({"name": check.gate, "status": status, "at": at,
                      "detail": detail})
    gates.append(_criteria_gate(payload, at))
    return gates


def merge_gates(existing: list, fresh: list[dict]) -> list[dict]:
    """This run's results supersede the previous run's, name by name.

    The column is a list and stays one -- names come from more than one
    producer and the order is the order they were produced in -- but a check
    that ran again is the SAME question answered later, not a second question.
    A reviewer surface showing `typecheck:python` twenty times, nineteen of
    them stale, is worse than one showing its current answer. Gate names this
    run did not produce are kept untouched, so a future producer's entries are
    not collateral damage of a reviewer re-run.

    PUBLIC because it is the rule for the whole column, not for this producer:
    `services/claim_check.py` (step C6) writes the same column and must
    supersede by exactly the same rule. Two copies of this would be two rules,
    and the second one would be the one that drifts.
    """
    names = {g["name"] for g in fresh}
    kept = [g for g in (existing or [])
            if isinstance(g, dict) and g.get("name") not in names]
    return kept + fresh


def record_reviewer_run(payload: dict, *, change_id: str | None = None,
                        project_slug: str | None = None,
                        branch_slug: str | None = None) -> dict | None:
    """Attach a reviewer run's per-check outcomes to its change.

    Addressed either by `change_id` or by the `(project_slug, branch_slug)`
    pair that every published entry point in `services/worktrees.py` takes --
    the sweep knows the branch before it knows the change id, and the reviewer
    knows the tree it is standing in.

    Returns the updated change, or None when there was nothing to write to.
    NEVER RAISES: see the module docstring. The reviewer's verdict is already
    made and is unaffected either way.
    """
    try:
        gates = gates_from_reviewer(payload)
    except Exception as exc:  # a malformed payload is not the sweep's problem
        print(f"[change_gates] unusable reviewer payload: {exc}", file=sys.stderr)
        return None
    try:
        # `changes` is reached lazily and `ownership` is not: this module is
        # ABOVE changes in the layering and importing it at module scope would
        # widen the backend's one big import cycle by a module that nothing
        # below needs. `ownership` has no service imports of its own, so it
        # costs nothing to name up front.
        from services import changes

        # Pin the data owner before the store, always. Without this the call
        # dies on OwnerUnset and the gates are silently never written -- the
        # exact failure the design doc's section 8 records against the
        # change-opening helper.
        ownership.pin_operator()

        if change_id:
            current = changes.get_change(change_id)
        elif project_slug and branch_slug:
            current = changes.change_for_branch(project_slug, branch_slug)
            if current is None:
                print(f"[change_gates] no open change for {project_slug}/"
                      f"{branch_slug}; gates not recorded", file=sys.stderr)
                return None
        else:
            print("[change_gates] no change id and no project/branch; "
                  "gates not recorded", file=sys.stderr)
            return None
        return changes.update_change(
            current["id"], gates=merge_gates(current.get("gates"), gates))
    except Exception as exc:
        print(f"[change_gates] gates not recorded ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """`python3 -m services.change_gates --change-id X < reviewer.json`.

    The entry point `reviewer.sh` uses. Exits 0 whatever happens, because its
    caller's contract is that a gate write cannot change the reviewer's own
    result.
    """
    ap = argparse.ArgumentParser(
        description="Attach reviewer.sh's per-check outcomes to a change.")
    ap.add_argument("--change-id", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--input", default="-",
                    help="reviewer JSON file, or - for stdin")
    args = ap.parse_args(argv)

    try:
        raw = (sys.stdin.read() if args.input == "-"
               else open(args.input, encoding="utf-8").read())
        payload = json.loads(raw)
    except Exception as exc:
        print(f"[change_gates] could not read the reviewer payload: {exc}",
              file=sys.stderr)
        return 0
    # The sweep hands over the whole receipt when that is what it has; the
    # reviewer's own run hands over its JSON directly. Accept both rather than
    # making the caller unwrap.
    if isinstance(payload, dict) and isinstance(payload.get("reviewer"), dict):
        payload = payload["reviewer"]

    written = record_reviewer_run(
        payload, change_id=args.change_id or None,
        project_slug=args.project or None, branch_slug=args.branch or None)
    if written is not None:
        print(f"[change_gates] {len(written['gates'])} gates recorded on "
              f"change {written['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
