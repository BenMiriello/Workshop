#!/usr/bin/env python3
"""Thin CLI over backend/services/tasks.py so shell (project_ralph.sh) and a
builder chat's Bash can drive a scope's structured task store WITHOUT desyncing
it from TODO.md.

The store (<scope>/.tasks.json) is authoritative; TODO.md is its regenerated
export plus a healed inbox. NEVER sed-edit TODO.md directly; a stray edit is
reconciled back in, but editing state that way is not the contract. Change state
through this CLI (shell) or the task API (chat/UI).

Verbs (all take a scope directory as the first positional):
  next-open <dir>                     first ready task as JSON (alias: next-ready)
  next-ready <dir> [--assignee A]     dependency-aware next ready task
  list-json <dir> [--assignee A] [--include-done]
  add <dir> "text" [--priority N] [--depends-on id,id] [--assignee A]
      [--touches glob,glob] --acceptance-cmd CMD | --criterion 'desc::cmd::expect'
      # ACCEPTANCE IS REQUIRED. A ticket with no command and no runnable
      # criterion is refused (exit 2) with the sentence that says what would
      # satisfy it: a filing whose finish line is prose leaves the worker to
      # invent one and the reviewer to guess at it. services/ticket_format owns
      # the rule; docs/ticket-writing.md says how to choose the check.
      [--max-attempts N] [--effort s|m|l]
      [--execution code|judgement|external] [--type bug|feature|task]
      # --execution is the UNATTENDED selection predicate. Unset = unclassified
      # = never picked up by a sweep (fail closed). It never narrows what a
      # human may run. Filing without it is why chat-filed tickets sat inert.
      # --type is the work CATEGORY, orthogonal to --execution. Unset = the
      # store does not invent one.
      [--discovered-from id] [--provenance KEY] [--source KIND] [--source-ref REF]
      # --source is WHERE this ticket came from: operator | chat | agent |
      # autogen | unknown. Unset records `unknown`, which is the same default
      # the API uses. It is not `agent` and it is not `operator`: guessing is
      # what made the field unreadable (see services/tasks SOURCE_KINDS).
      # --source-ref is what the ticket came FROM (a chat thread id for
      # --source chat). Omit it and the ticket is orphaned: nothing links it
      # back to the conversation that asked for it.
      # --criterion is repeatable and attaches a machine-checkable assertion the
      # gate EXECUTES (exit0 by default, or an expected stdout substring). The
      # 'desc::' and '::expect' segments are optional: 'cmd' alone => exit0 gate.
  set-status <dir> <id> <status> [--job-id J] [--reason R] [--expect-claim-token T]
  claim <dir> <id> <job_id> [--assignee A] [--pid PID]
  heartbeat <dir> <id> <claim_token>  renew a claimed task's lease (fenced by token)
  note <dir> <id> "evidence text" [--kind KIND] [--ref REF]
  block <dir> <id> "reason" [--expect-claim-token T]
      [--kind K] [--ask "..."] [--unblocks-if "..."] [--dedupe-key KEY]
      [--proposal-uri URI --proposal-verb read|write|append] [--raised-by REF]
      # The structured request the Review tab answers. `reason` alone still works
      # (a plain block). --kind is an open slug (permission|resource|input|
      # decision|missing-info|dependency|environment|other, or a new one): the set
      # is a seed, not a boundary. A --proposal-uri + --proposal-verb pair makes
      # the ask machine-checkable, so the review can offer a prefilled grant
      # instead of asking the operator to author one. --dedupe-key groups a
      # mass-block so ten tasks stopped by one rule are answered once.
  set-awaiting <dir> <id> "question"  set/clear the operator-input payload
  check-touches <dir> <id> [--expect-claim-token T]
      read a newline-separated file list on STDIN and hold the run to the task's
      declared `touches` globs: prints {"violations": [...], "blocked": bool}. A
      task with no `touches` is unrestricted, so this prints an empty list.

Every command prints JSON on stdout (a task object, a list, or {} / {"error":...}).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# backend/ on path so `from services import tasks` resolves the SAME store the API uses.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

# RUN UNDER THE BACKEND'S INTERPRETER, or this CLI writes to a store nothing
# reads. The two migrated scopes read their tickets from Postgres; `tasks`
# reaches that through sqlalchemy, and when the import is missing it falls back
# to the file. Under the system python3 that fallback is silent: the command
# prints the updated record and exits 0, the file says `done`, Postgres still
# says `todo`, and the app shows `todo`. Reproduced 2026-08-15 by closing a
# ticket with `python3 scripts/project_tasks.py set-status ... done`.
#
# Every doc, prompt and script spells the invocation `python3
# scripts/project_tasks.py`, so making that spelling correct is the fix, rather
# than asking thirty callers to remember a path. Re-exec once (the env flag is
# the guard) into backend/.venv, and only when this interpreter cannot do the
# job. A checkout with no venv still runs, file-backed, exactly as before.
if (importlib.util.find_spec("sqlalchemy") is None
        and not os.environ.get("WORKSHOP_TASKS_CLI_REEXEC")):
    _VENV_PY = _BACKEND / ".venv" / "bin" / "python3"
    if _VENV_PY.exists():
        os.environ["WORKSHOP_TASKS_CLI_REEXEC"] = "1"
        os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

from services import tasks, ticket_format  # noqa: E402

# What to type instead, on THIS command. `ticket_format.ACCEPTANCE_HINT` says
# what an acceptance is; this says how to spell one here.
_ACCEPTANCE_FIX = (
    "add --acceptance-cmd 'npm run typecheck' for a single command, or repeat "
    "--criterion 'the suite passes::pytest -q::exit0' for assertions the gate "
    "executes. Both may be given."
)


def _pin_owner() -> None:
    """Pin the data owner, or the two migrated scopes fall back to the file.

    `tasks._from_relational` needs an owner to resolve a tenant, and with none
    pinned it logs the failure and reads the file instead. In a CLI that is a
    silent wrong answer rather than an error: `set-status ... done` writes
    `done` to a file nothing reads while the app keeps showing `todo`. Every
    other automation set-point (db/shadow.py, db/chat_list_parity.py) pins an
    owner for exactly this reason; this is the one that had not.

    The rule now lives in `services/ownership.pin_operator` rather than in this
    comment, because it was true a second time of a different caller: the inline
    python in scripts/project_ralph.sh had no pin either, and a sweep launched
    outside the backend died on OwnerUnset.

    The id is LOOKED UP, never hardcoded: `auth_store.operator_id()` is the same
    data lookup the bootstrap migration uses, so a fresh install with a
    different id works and nothing machine-specific enters the source. Missing
    auth store, or a checkout with no database, leaves the owner unpinned and
    the file-backed behaviour exactly as it was.
    """
    try:
        from services import ownership
        ownership.pin_operator()
    except Exception:
        pass  # no auth store: file-backed scopes still work unchanged


# `ticket.spec_hash` is character varying(64). Spelled here so the refusal can
# name the real limit rather than a guess.
_PROVENANCE_MAX = 64


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _parse_criterion(raw: str) -> dict | None:
    """Parse a `desc::cmd::expect` criterion string into {desc, cmd, expect}.

    Segments are optional: `cmd` alone => {desc: cmd, cmd, expect: exit0};
    `desc::cmd` => expect defaults to exit0; `desc::cmd::expect` sets all three.
    When 3+ segments are present the FIRST is desc and the LAST is expect, so a
    cmd containing `::` still parses. A blank cmd yields None (dropped)."""
    parts = [p for p in (raw or "").split("::")]
    if len(parts) >= 3:
        desc, expect = parts[0].strip(), parts[-1].strip()
        cmd = "::".join(parts[1:-1]).strip()
    elif len(parts) == 2:
        desc, cmd, expect = parts[0].strip(), parts[1].strip(), ""
    else:
        cmd = (parts[0] if parts else "").strip()
        desc, expect = "", ""
    if not cmd:
        return None
    return {"desc": desc or cmd, "cmd": cmd, "expect": expect or "exit0"}


# PRIORITY IS A BAND, NOT A NUMBER, and this is where that stops being a trap.
# The schema stores one of `urgent | high | medium | low | none`
# (db/models.PRIORITIES). The integer this API still speaks is a projection at
# the floor of each band, so `--priority 78` was silently stored as `medium` and
# read back as 40. Five separate agents filed carefully graded priorities in one
# day and every one of them collapsed, because nothing said so.
#
# So: a band NAME is the documented input. An integer is still accepted, because
# 18 callers pass one, but it is mapped through the same table the store uses and
# the result is REPORTED rather than applied silently.
_PRIORITY_BANDS = ("urgent", "high", "medium", "low", "none")
_BAND_FLOOR = {"urgent": 100, "high": 80, "medium": 40, "low": 1, "none": 0}


def _band_of(n: int) -> str:
    for band in _PRIORITY_BANDS:
        if n >= _BAND_FLOOR[band]:
            return band
    return "none"


def _priority_arg(raw: str):
    """A band name, or an integer mapped to its band with a note on stderr."""
    text = str(raw).strip().lower()
    if text in _BAND_FLOOR:
        return _BAND_FLOOR[text]
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"priority must be one of {', '.join(_PRIORITY_BANDS)} "
            f"(an integer is accepted and mapped to its band); got {raw!r}")
    band = _band_of(n)
    if n != _BAND_FLOOR[band]:
        print(f"note: priority {n} is stored as {band!r}; priority is a band, "
              f"not a scale, so {n} and {_BAND_FLOOR[band]} are the same value.",
              file=sys.stderr)
    return _BAND_FLOOR[band]


def _resolve_task_id(root: Path, given: str) -> str | dict:
    """The full id for `given`, or an error dict if it names no single task.

    WHY THIS EXISTS. `tasks.update` answers None for an id it does not hold, and
    every verb here prints `_slim(None)`, which is `{}`, and returns 0. So a
    wrong id was a SILENT SUCCESS: the CLI said nothing, exited clean, and wrote
    nothing. Measured 2026-09-03 on ticket df91b7c4 -- `note` with the 8-char id
    printed `{}`, exited 0, and left `evidence` empty; the same call with the
    32-char id worked. Eight characters is the id form that TODO.md, the ticket
    lists, the reviewer output and every handoff document actually display, so
    the failing form is the one an agent naturally types, and the annotations it
    dropped were never missed by anyone.

    Two fixes in one: a unique prefix RESOLVES, because a short id is the
    ergonomic thing to type and refusing it would just move the papercut; and an
    id that matches nothing, or matches more than one task, is an ERROR with a
    non-zero exit rather than an empty object.
    """
    given = (given or "").strip()
    if not given:
        return {"error": "no task id given"}
    try:
        rows = tasks.list_tasks(root, include_done=True)
    except Exception as exc:                      # the store is the authority;
        return {"error": f"could not read the task store: {exc}"}
    ids = [t.get("id", "") for t in rows]
    if given in ids:
        return given                              # an exact id always wins
    hits = [i for i in ids if i.startswith(given)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return {"error": f"no task whose id is or starts with {given!r}",
                "fix": "check the id against: project_tasks.py list-json ."}
    return {"error": f"{given!r} is ambiguous: it matches {len(hits)} tasks",
            "matches": hits[:8],
            "fix": "give more of the id"}


def _slim(t: dict | None) -> dict:
    """Compact projection for the loop (keeps next-open's old shape + new fields)."""
    if not t:
        return {}
    return {
        "id": t["id"], "text": t.get("text", ""), "owner": t.get("owner", "operator"),
        "assignee": t.get("assignee"), "status": t.get("status"),
        "priority": t.get("priority", 0),
        # The band is the stored truth; the integer above is its floor.
        "priority_band": _band_of(int(t.get("priority") or 0)),
        "depends_on": t.get("depends_on", []),
        "acceptance": t.get("acceptance"), "max_attempts": t.get("max_attempts"),
        "attempts": t.get("attempts", 0), "awaiting": t.get("awaiting"),
        "blocked_reason": t.get("blocked_reason"),
        "touches": t.get("touches", []),
        "effort": t.get("effort"),
        "claim_token": t.get("claim_token"),
        "lease_until": t.get("lease_until"),
        "last_heartbeat": t.get("last_heartbeat"),
        "claim_pid": t.get("claim_pid"),
    }


# What OMITTING each flag means, so the error can say it rather than make the
# caller guess. Absence is a supported choice at every one of these; an empty
# value is not a way to express it.
_ABSENCE_MEANS = {
    "--expect-claim-token": "Omit the flag entirely to make an operator write, which is never fenced",
    "claim_token": "A heartbeat has no unfenced form; pass the token the claim returned",
    "--pid": "Omit the flag entirely to record no pid",
}


def _opt_int(v, flag: str):
    """An optional int CLI arg: OMITTED -> None. PRESENT -> must parse, or error.

    Omitting the flag is a real, supported choice and keeps its meaning exactly:
    for `--expect-claim-token` it means "this is an operator write, do not fence
    it", which is what lets an operator take a ticket back from a worker; for
    `--pid` it means "no pid recorded". A flag that is PRESENT but unparseable is
    a different thing, a caller bug, and is refused.

    THE EMPTY STRING IS THE CASE THAT MATTERS, because that is what a shell
    produces from "${VAR:-}" when VAR is unset, and every fence call site is
    written that way. This used to map "" to None, which did not merely fail to
    fence: `tasks.update` reads `expect_claim_token is None` as an operator
    write, so an empty token SILENTLY UPGRADED the call's authority past what it
    asked for. It got worse from there, because update's Fix 1 then saw an
    unfenced material edit to a ticket a worker held and bumped the generation,
    fencing out the very run that made the call while its status change still
    landed. Both halves were silent: nothing logged, and the exit code was 0, so
    a caller could not tell a fence that passed from a fence never applied.

    THE SAME COERCION ON `--pid` was deliberately considered and gets the same
    answer. A malformed pid read as "no pid recorded" changes which tickets
    `reclaim` considers stale, so it is an error too. Only true absence is
    optional, and for the same reason in both cases: absence is a decision the
    caller made, an unparseable value is one it did not.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        raise ValueError(
            f"{flag} was given an empty value. "
            f"{_ABSENCE_MEANS.get(flag, 'Omit the flag to leave it unset')}, but an "
            f"empty value is not the way to say so: it used to be read as absence, "
            f"silently.")
    try:
        return int(s)
    except (TypeError, ValueError):
        raise ValueError(f"{flag} expects an integer, got {v!r}")


def main(argv: list[str]) -> int:
    _pin_owner()
    p = argparse.ArgumentParser(prog="project_tasks.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scope(sp):
        sp.add_argument("dir", type=Path)

    sp = sub.add_parser("next-open"); scope(sp); sp.add_argument("--assignee", default=None)
    sp = sub.add_parser("next-ready"); scope(sp); sp.add_argument("--assignee", default=None)
    # --unattended adds the two phase-4 selection rules: execution == "code" and
    # the readiness check passes. Passed by the SWEEP and by nothing else, so
    # that "readiness never vetoes a human" and "only ready tickets run
    # unattended" can both be true at once.
    sp.add_argument("--unattended", action="store_true")
    sp = sub.add_parser("list-json"); scope(sp)
    sp.add_argument("--assignee", default=None); sp.add_argument("--include-done", action="store_true")

    sp = sub.add_parser("add"); scope(sp); sp.add_argument("text")
    sp.add_argument("--priority", type=_priority_arg, default=0,
                    metavar="{urgent,high,medium,low,none}",
                    help="a band name. An integer is accepted and mapped to its "
                         "band, which is what actually gets stored.")
    sp.add_argument("--depends-on", default=None)
    sp.add_argument("--assignee", default=None)
    sp.add_argument("--touches", default=None)
    sp.add_argument("--acceptance-cmd", default=None)
    sp.add_argument("--criterion", action="append", default=None,
                    help="repeatable machine-checkable assertion 'desc::cmd::expect' "
                         "(expect defaults to exit0; a bare 'cmd' is allowed)")
    sp.add_argument("--max-attempts", type=int, default=None)
    sp.add_argument("--effort", default=None)
    sp.add_argument("--discovered-from", default=None)
    sp.add_argument("--provenance", default=None)
    sp.add_argument("--execution", default=None,
                    choices=("code", "judgement", "external"),
                    help="can a machine do this at all: code (a change to a "
                         "tree a worker can write) | judgement (a decision, "
                         "design or audit) | external (needs root, another "
                         "host user, a vendor console). Unset means "
                         "unclassified, which is INELIGIBLE for unattended "
                         "dispatch. Fail-closed on purpose.")
    sp.add_argument("--type", default=None,
                    choices=("bug", "feature", "task"),
                    help="the work CATEGORY, orthogonal to --execution: bug "
                         "(something is broken) | feature (a new capability) | "
                         "task (any other defined work: refactor, wiring, "
                         "migration, investigation, routine maintenance). "
                         "Unset means unclassified; the store never invents a "
                         "type.")
    # `unknown` when nothing is said, the SAME default the API door uses
    # (services/tasks.add). It used to default to `agent` here while the API
    # defaulted to `operator`, so one filing got two different provenances
    # depending on which door it came through and `operator` doubled as the
    # value for "nobody recorded anything". Say who asked; nothing infers it.
    sp.add_argument("--source", default="unknown",
                    choices=("operator", "chat", "agent", "autogen", "unknown"),
                    help="where this ticket came FROM. Unset records nothing, "
                         "which is honest and is not the same as `operator`.")
    sp.add_argument("--source-ref", default=None,
                    help="what this ticket came FROM: a chat thread id for "
                         "--source chat. Without it the ticket is orphaned and "
                         "neither the ticket page nor the chat can link to the "
                         "other.")

    sp = sub.add_parser("set-status"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("status")
    sp.add_argument("--job-id", default=None); sp.add_argument("--reason", default=None)
    sp.add_argument("--expect-claim-token", default=None)
    sp.add_argument("--restore-attempt", action="store_true",
                    help="give back the attempt the claim consumed (env failure, not task failure)")

    sp = sub.add_parser("claim"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("job_id"); sp.add_argument("--assignee", default=None)
    sp.add_argument("--pid", default=None)

    sp = sub.add_parser("heartbeat"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("claim_token")
    sp.add_argument("--assignee", default=None)

    sp = sub.add_parser("note"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("text")
    sp.add_argument("--kind", default="note"); sp.add_argument("--ref", default=None)

    sp = sub.add_parser("block"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("reason")
    sp.add_argument("--expect-claim-token", default=None)
    sp.add_argument("--kind", default=None)
    sp.add_argument("--ask", default=None)
    sp.add_argument("--unblocks-if", default=None)
    sp.add_argument("--proposal-uri", default=None)
    sp.add_argument("--proposal-verb", default=None, choices=(None, "read", "write", "append"))
    sp.add_argument("--dedupe-key", default=None)
    sp.add_argument("--raised-by", default=None)

    sp = sub.add_parser("set-awaiting"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("question")

    sp = sub.add_parser("check-touches"); scope(sp)
    sp.add_argument("task_id")
    sp.add_argument("--job-id", default=None)
    sp.add_argument("--expect-claim-token", default=None)

    a = p.parse_args(argv[1:])
    root: Path = a.dir

    # A MALFORMED FLAG IS REFUSED BEFORE THE ID IS LOOKED UP. `--expect-claim-token ""`
    # is a caller bug whether or not the id exists, and it has its own exit code
    # (1, a bad argument) distinct from an unknown id (2). Resolving the id first
    # meant an empty token on an id that happened not to exist reported "no such
    # ticket" and exited 2, hiding the more serious of the two problems -- the
    # one that used to SILENTLY UPGRADE the call's authority. Caught by
    # tests/test_fence_token_is_never_promoted.py when the unknown-id check was
    # added ahead of it (1d364164).
    if getattr(a, "expect_claim_token", None) is not None:
        try:
            _opt_int(a.expect_claim_token, "--expect-claim-token")
        except ValueError as e:
            print(json.dumps({"error": str(e)}))
            return 1

    if getattr(a, "task_id", None):
        resolved = _resolve_task_id(root, a.task_id)
        if isinstance(resolved, dict):
            print(json.dumps(resolved))
            return 2
        a.task_id = resolved

    try:
        if a.cmd in ("next-open", "next-ready"):
            print(json.dumps(_slim(tasks.next_ready(
                root, assignee=a.assignee,
                unattended=bool(getattr(a, "unattended", False))))))
            return 0
        if a.cmd == "list-json":
            print(json.dumps(tasks.list_tasks(root, include_done=a.include_done, owner=a.assignee)))
            return 0
        if a.cmd == "add":
            acceptance = {"kind": "command", "spec": a.acceptance_cmd} if a.acceptance_cmd else None
            criteria = [c for c in (_parse_criterion(r) for r in (a.criterion or [])) if c]
            if criteria:
                # A criteria-carrying task with no explicit command acceptance stays
                # reviewer-kind: the assertions run alongside the reviewer's diff gate.
                acceptance = acceptance or {"kind": "reviewer", "spec": None}
                acceptance["criteria"] = criteria
            # A TICKET WITHOUT AN EXECUTABLE FINISH LINE IS NOT A TICKET.
            # Refused HERE as well as in the store's own filing path, because
            # this is the surface an agent actually types at: the message has to
            # name the two flags that would satisfy it, on the command that was
            # just refused, or the next attempt is another guess. See
            # services/ticket_format and docs/ticket-writing.md.
            refusal = ticket_format.acceptance_refusal(acceptance)
            if refusal:
                print(json.dumps({"error": refusal, "fix": _ACCEPTANCE_FIX}))
                return 2
            # `--provenance` is a KEY, not prose. It lands in `ticket.spec_hash`,
            # `character varying(64)`, and a longer value reached psycopg and
            # came back as a StringDataRightTruncation traceback with the whole
            # INSERT and every parameter inlined -- including the ticket body.
            # The filing was lost and the reason was buried in 30 lines of SQL.
            # Refused here, with the limit and the actual length, because this
            # is the surface an agent types at and a stack trace is not an
            # error message.
            if a.provenance and len(a.provenance) > _PROVENANCE_MAX:
                print(json.dumps({
                    "error": f"--provenance is a short KEY, not prose: "
                             f"{len(a.provenance)} chars, limit {_PROVENANCE_MAX}",
                    "fix": "pass a stable identifier (a ticket id, a doc slug, a "
                           "check name). Put the explanation in the ticket body, "
                           "which has no such limit.",
                }))
                return 2
            t = tasks.add(
                root, text=a.text, priority=a.priority, depends_on=_csv(a.depends_on),
                assignee=a.assignee, touches=_csv(a.touches), acceptance=acceptance,
                max_attempts=a.max_attempts, effort=a.effort,
                execution=a.execution,
                type=a.type,
                discovered_from=a.discovered_from, provenance=a.provenance,
                # ref is what makes a ticket navigable back to where it came
                # from: the ticket page links to the chat, and the chat lists
                # the work it filed. Hardcoding None here (until 2026-08-14)
                # meant every ticket filed from a chat through this CLI arrived
                # orphaned, while the same ticket filed through the API kept its
                # thread id. That is why the August tickets are unlinked and the
                # July ones are not.
                source={"kind": a.source, "ref": a.source_ref},
            )
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "set-status":
            t = tasks.update(root, a.task_id, status=a.status, job_id=a.job_id,
                             blocked_reason=a.reason,
                             restore_attempt=bool(getattr(a, "restore_attempt", False)),
                             expect_claim_token=_opt_int(a.expect_claim_token,
                                                         "--expect-claim-token"))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "claim":
            t = tasks.claim(root, a.task_id, a.job_id, assignee=a.assignee,
                            pid=_opt_int(a.pid, "--pid"))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "heartbeat":
            t = tasks.heartbeat(root, a.task_id,
                                _opt_int(a.claim_token, "claim_token"),
                                assignee=a.assignee)
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "note":
            t = tasks.update(root, a.task_id, evidence={"kind": a.kind, "ref": a.ref or a.text})
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "block":
            # `reason` is the human line and stays exactly as it was. --ask (and
            # friends) additionally attach the STRUCTURED request the Review tab
            # acts on: what would unblock this, and — when the ask names a uri and
            # a verb — a grant the operator can approve in one click. Omit them and
            # the block is what it always was.
            request = None
            if a.ask or a.kind:
                request = {
                    "kind": a.kind or "other",
                    "ask": a.ask or a.reason,
                    "question": a.ask or a.reason,
                    "why": a.reason,
                    "unblocks_if": a.unblocks_if,
                    "dedupe_key": a.dedupe_key,
                    "raised_by": a.raised_by,
                }
                if a.proposal_uri and a.proposal_verb:
                    request["proposal"] = {"uri": a.proposal_uri, "verb": a.proposal_verb}
            t = tasks.update(root, a.task_id, status="blocked", blocked_reason=a.reason,
                             blocked_request=request,
                             expect_claim_token=_opt_int(a.expect_claim_token,
                                                         "--expect-claim-token"))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "set-awaiting":
            t = tasks.update(root, a.task_id, awaiting={"question": a.question})
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "check-touches":
            files = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
            bad = tasks.enforce_touches(
                root, a.task_id, files, job_id=a.job_id,
                expect_claim_token=_opt_int(a.expect_claim_token,
                                            "--expect-claim-token"))
            print(json.dumps({"violations": bad, "blocked": bool(bad)}))
            return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps({"error": f"unknown command: {a.cmd}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
