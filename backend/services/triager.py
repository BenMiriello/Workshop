"""Triager — the note-triage runner (final-plan P4 / roles report 3.3 + 4.5).

TWO PASSES LIVE HERE, and they share only the admission and the caps.
`run_triage` is the NOTE pass, described below and unchanged. `run_queue_triage`
(bottom of the file, autonomy-implementation 2026-08-13 §5.2) is the QUEUE pass:
it reads `todo` TICKETS, asks `ticket_scope_review` whether each is the right
size and shape, and records the answer. They are kept apart on purpose, because
the one thing that makes the note pass safe (it cannot write notes, so it cannot
change its own trigger input) is exactly the thing the queue pass cannot have.
See the section header above `QUEUE_DECISIONS` for what replaces it.

A `triager` reads the notes in ONE knowledge scope and decides, per note, exactly
one of four things. That closed set is the whole role::

    task      file a Task (source.kind=agent, provenance=note:<scope>:<id>)
    link      attach the note to an existing Task as evidence
    escalate  put the question on a Task as an `awaiting` block
    none      a deliberate no-op, recorded with a reason

It may NOT write code, NOT write notes, NOT issue verdicts, and NOT answer on the
operator's behalf. The "may not write notes" clause is not a prompt instruction:
it is structural. This module imports no note-writing path (`knowledge.put_note`
is never referenced), and its one model call runs with ``tools_disabled=True``, so
the model has no tool to write with. That is the loop-safety guarantee — a triager
that cannot write notes cannot change its own trigger input and re-fire itself.

The ask this serves (D1)
------------------------
Writing the note must be the WHOLE interaction. No tag, no `@triage`, no special
folder, no frontmatter flag. Everything here is therefore constrained to be
invisible from the note-writing side, which has one hard consequence:

    **Triage state NEVER goes in the note file.** The note is byte-identical
    before and after a pass.

Addressed-ness is recorded in two places instead, neither of which is the note:
  * the Task's ``provenance == note:<scope>:<id>`` (durable, free, and already the
    dedupe key — `tasks.py`), and
  * a sidecar (`.runtime/triager/<scope>.json`), which is needed because
    provenance cannot express `none` ("I read this and deliberately did nothing").

Why the sidecar rather than `.runtime/trigger-state.json` (report step 2's key
shape): trigger-state.json is read-modify-written wholesale by the dispatcher on
every tick. A detached runner writing it concurrently would be clobbered by the
next tick's `save_state`. One file, one writer — the same call the memory-keeper
made for its own watermark.

Termination
-----------
One pass per activation. Notes are taken oldest-first, capped at
``MAX_NOTES_PER_PASS``; filing stops at ``MAX_TASKS_PER_PASS``. Notes past a cap
simply stay due for the next tick, because **the watermark advances only over
notes actually decided** (the commit-only-if-dispatched pattern the dispatcher
already uses for cron firings). A pass that crashes leaves the watermark where it
was.

Failure mode: TICKET SPAM
-------------------------
Notes are cheap to write and tasks are expensive to work. A triager that files one
task per note turns a scratchpad into a backlog and the operator stops leaving
notes, which defeats D1 entirely. Four guards, in order of strength:
  1. `none` is the DEFAULT the persona and this module are pushed toward — a note
     is presumed to be a note. An unparseable model answer decides `none`.
  2. ``MAX_TASKS_PER_PASS`` bounds the worst case.
  3. The two-stage dedupe already shipped for task creation (`task_dedup`:
     embeddings for recall, a local LLM for the decision, non-destructive merge)
     is reused UNCHANGED. A `task` decision that duplicates existing work comes
     back `merge` and is recorded as a `link`.
  4. `provenance=note:<scope>:<id>` makes re-filing the same note idempotent at
     the store level (`tasks.add` returns the existing task on a provenance hit)
     and lets the operator see note-filed tasks as a class.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from services import knowledge, task_dedup, task_scope, tasks

# Per-pass caps (roles report 3.3: "recommend 20" / "recommend 5").
MAX_NOTES_PER_PASS = 20
MAX_TASKS_PER_PASS = 5

# The CLOSED set of outcomes. This is what makes "ensure all notes are addressed"
# mechanically checkable rather than aspirational: addressed means has a decision.
DECISIONS = ("task", "link", "escalate", "none")

# Rules may only name a decision that needs no target. `link` needs an existing
# task id, which a textual rule cannot know; a rule that "links" is a rule that
# does nothing, so it is rejected at parse time rather than silently dropped.
RULE_DECISIONS = ("task", "escalate", "none")

# How much of a note the model sees. A note is a note, not a document; the head is
# what carries the intent, and an unbounded body would blow the per-note call.
_NOTE_CHARS = 4000

# `- pattern -> decision: reason`, with the leading bullet and the reason optional.
_RULE_RE = re.compile(
    r"^\s*[-*]?\s*(?P<pat>.+?)\s*->\s*(?P<dec>[a-z]+)\s*(?::\s*(?P<why>.*?))?\s*$"
)
_REGEX_RULE_RE = re.compile(r"^/(?P<body>.*)/(?P<flags>[i]*)$")


class ClosedSetError(ValueError):
    """A decision outside the closed set of four. Raised, never coerced, on the
    apply path: a fifth outcome means a caller invented authority this role does
    not have, and swallowing it would make the closed set a suggestion."""


def validate_decision(value: object) -> str:
    """The canonical decision, or raise. The closed set is enforced HERE, once, on
    the path that actually acts."""
    if not isinstance(value, str) or value.strip().lower() not in DECISIONS:
        raise ClosedSetError(
            f"decision must be one of {DECISIONS}; got {value!r}"
        )
    return value.strip().lower()


# --- sidecar state -----------------------------------------------------------

def _state_dir() -> Path:
    # No eager mkdir: a dry-run reads state and must create nothing.
    # Per-owner: this is watermark/proposal state ABOUT one person's scopes.
    from services import ownership
    return ownership.workspace_path(
        knowledge._runtime() / "triager", "triager")


def _safe_scope(scope_key: str) -> str:
    """A scope key as ONE filename component — see memory_keeper._safe_scope,
    which had the identical defect: `:` -> `-` left `/` and `..` intact, and
    `state_path`'s caller writes to whatever comes back."""
    from services import learnings
    return learnings._valid_scope(scope_key).replace(":", "-")


def state_path(scope_key: str) -> Path:
    return _state_dir() / f"{_safe_scope(scope_key)}.json"


def read_state(scope_key: str) -> dict:
    p = state_path(scope_key)
    try:
        data = json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("scope", scope_key)
    data.setdefault("watermark", 0.0)
    data.setdefault("notes", {})
    return data


def _write_state(state: dict) -> None:
    p = state_path(state["scope"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --- the ready predicate (cheap: stat only, no model, no store read) ---------

def due_notes(scope_key: str) -> list[dict]:
    """Notes in the scope modified since the watermark, oldest-first.

    Oldest-first is what makes the caps safe: the notes a capped pass decides are
    the oldest ones, so every note it skips has a NEWER mtime than the watermark
    it leaves behind and is therefore still due next tick.
    """
    knowledge._parse_scope(scope_key)
    state = read_state(scope_key)
    mark = float(state.get("watermark") or 0.0)
    d = knowledge.scope_dir(scope_key)
    out: list[dict] = []
    for f in sorted(d.glob("*.md")):
        if f.stem == "MEMORY" or not knowledge._NOTE_ID_RE.fullmatch(f.stem):
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime <= mark:
            continue
        out.append({"id": f.stem, "path": f, "mtime": mtime})
    out.sort(key=lambda n: (n["mtime"], n["id"]))
    return out


def has_due_notes(scope_key: str) -> bool:
    """The dispatcher's ready predicate. Stat-only, so a scope with nothing new
    costs a directory listing per tick and no model call at all."""
    try:
        return bool(due_notes(scope_key))
    except Exception:
        return False


# --- rules (deterministic, run BEFORE the model) -----------------------------

def parse_rules(body: str) -> list[dict]:
    """Textual routing rules out of the agent's own definition body.

    Only lines inside a `## Rules` section are read, so the rest of the persona is
    prose the model reads and this parser ignores. Each rule is::

        - <substring> -> <decision>: <reason>
        - /<regex>/i  -> <decision>: <reason>

    Rules run first (the Dependabot precedent, report 2.6): a rule that matches
    decides without a model call. An unparseable or out-of-set line is skipped —
    a typo in a persona must not take the runner down.
    """
    rules: list[dict] = []
    in_section = False
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            in_section = line.strip("# ").strip().lower().startswith("rules")
            continue
        if not in_section or not line.strip():
            continue
        m = _RULE_RE.match(line)
        if not m:
            continue
        dec = m.group("dec").strip().lower()
        if dec not in RULE_DECISIONS:
            continue
        pat = m.group("pat").strip().strip("`")
        rm = _REGEX_RULE_RE.match(pat)
        if rm:
            try:
                flags = re.IGNORECASE if "i" in (rm.group("flags") or "") else 0
                rx = re.compile(rm.group("body"), flags | re.MULTILINE)
            except re.error:
                continue
            rules.append({"kind": "regex", "rx": rx, "decision": dec,
                          "reason": (m.group("why") or "").strip(), "raw": pat})
        else:
            if not pat:
                continue
            rules.append({"kind": "substring", "needle": pat.lower(), "decision": dec,
                          "reason": (m.group("why") or "").strip(), "raw": pat})
    return rules


def apply_rules(rules: list[dict], text: str) -> dict | None:
    """The first matching rule's decision, or None (hand it to the model)."""
    low = (text or "").lower()
    for r in rules:
        hit = r["rx"].search(text or "") if r["kind"] == "regex" else (r["needle"] in low)
        if hit:
            return {
                "decision": r["decision"],
                "reason": r["reason"] or f"rule matched: {r['raw']}",
                "by": "rule",
            }
    return None


# --- the model decision (one call per residual note, no tools) ---------------

_PROMPT = """You are the note triager for the knowledge scope `{scope}`.

Your ONLY job is to decide what, if anything, this note means for the work queue.
You decide exactly one of four things and nothing else. You do not write code, do
not write notes, do not judge anyone's work, and do not answer the note's question
yourself.

  task:     the note describes work that must happen and is not already queued.
  link:     the note restates, refines, or adds evidence to an OPEN task below.
  escalate: the note raises something this scope cannot decide; it needs the
            operator to answer a specific question.
  none:     everything else.

`none` IS THE DEFAULT AND THE COMMON CASE. A note is presumed to be a note: a
thought, a link, a reference, a fact, an observation, a half-formed idea. Notes
are cheap to write and tasks are expensive to work, so filing a task for a note
that was only a thought is the worst mistake you can make here. Choose `task`
only when the note names work someone must actually do. When in doubt: `none`.

{rules_block}
OPEN TASKS IN THIS SCOPE (for `link`; use the exact id):
{tasks_block}

NOTE `{note_id}`:
---
{note}
---

Reply with ONLY a JSON object, no prose and no code fence:
  {{"decision": "task"|"link"|"escalate"|"none",
    "reason": "<one short clause: why this decision>",
    "text": "<imperative one-line task, ONLY when decision is task>",
    "task_id": "<an id from the list above, ONLY when decision is link>",
    "question": "<the specific question for the operator, ONLY when decision is escalate>"}}
"""


def _decide_llm(scope_key: str, note_id: str, note: str, open_tasks: list[dict],
                rules_text: str) -> dict:
    """One `claude -p` call for one note. ``tools_disabled=True`` is load-bearing,
    not an optimization: it is why the model in this role has no way to write a
    note (or anything else) even if the persona were subverted.

    Never raises. Any failure — import, CLI, timeout, unparseable JSON, a decision
    outside the closed set — decides `none`, because the safe failure for a triager
    is to leave a note alone."""
    tasks_block = "\n".join(
        f"- {t['id']}: {(t.get('text') or '').strip()[:160]}" for t in open_tasks
    ) or "(none)"
    rules_block = (
        f"ROUTING RULES FROM YOUR OWN DEFINITION (they outrank your judgement):\n{rules_text}\n\n"
        if rules_text.strip() else ""
    )
    prompt = _PROMPT.format(
        scope=scope_key, note_id=note_id, note=note[:_NOTE_CHARS],
        tasks_block=tasks_block, rules_block=rules_block,
    )
    try:
        from services import llm_router
        res = llm_router.call_cloud(
            prompt, model=llm_router.TIER_HAIKU, tools_disabled=True,
            max_turns=1, timeout_s=120,
        )
    except Exception as e:
        return {"decision": "none", "reason": f"triage model unavailable ({e})", "by": "fallback"}
    text = (res or {}).get("text") or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"decision": "none", "reason": "triage model returned no decision", "by": "fallback"}
    try:
        raw = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"decision": "none", "reason": "triage model returned unparseable JSON", "by": "fallback"}
    if not isinstance(raw, dict):
        return {"decision": "none", "reason": "triage model returned a non-object", "by": "fallback"}
    try:
        decision = validate_decision(raw.get("decision"))
    except ClosedSetError:
        # Pushed toward `none` (guard 1): an invented outcome is not an error the
        # operator should have to see, it is a note left alone.
        return {"decision": "none", "reason": f"triage model proposed {raw.get('decision')!r}, outside the closed set", "by": "fallback"}
    out = {
        "decision": decision,
        "reason": str(raw.get("reason") or "").strip()[:300] or "(no reason given)",
        "by": "model",
    }
    for k in ("text", "task_id", "question"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


# --- apply -------------------------------------------------------------------

def _task_root(scope_key: str) -> Path | None:
    """The task store a scope's triage files into.

    `task_scope` owns workshop / project: / agent:. An `app:<name>` knowledge
    scope has no task store of its own (apps live inside this repo), so its work
    files into the PARENT scope — the workshop root queue — which is exactly what
    the role spec means by "file into the parent scope"."""
    root = task_scope.root_for_context(scope_key)
    if root is None and scope_key.startswith("app:"):
        root = task_scope.WORKSHOP_ROOT
    return root


def _note_title(text: str, note_id: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:120]
    return note_id


def apply_decision(root: Path, scope_key: str, note_id: str, note_text: str,
                   decision: dict, *, job_id: str | None, agent_slug: str | None,
                   open_tasks: list[dict]) -> dict:
    """Act on ONE validated decision. Returns the outcome record for the sidecar.

    The closed set is enforced here (`validate_decision` raises on a fifth), and
    every write in this function is a TASK write. There is deliberately no branch
    that touches the note.
    """
    kind = validate_decision(decision.get("decision"))
    reason = str(decision.get("reason") or "").strip()
    provenance = f"note:{scope_key}:{note_id}"
    src = {"kind": "agent", "ref": job_id or (f"agent:{agent_slug}" if agent_slug else "triager")}
    out = {"decision": kind, "reason": reason, "by": decision.get("by") or "model",
           "ts": _now_iso(), "task_ids": []}

    if kind == "none":
        return out

    if kind == "link":
        tid = str(decision.get("task_id") or "").strip()
        if not any(t["id"] == tid for t in open_tasks):
            # The model named a task that is not open in this scope. Linking to
            # nothing is not a fifth outcome, it is a `none` with a reason.
            out["decision"] = "none"
            out["reason"] = f"proposed link to unknown task {tid!r}; left alone"
            return out
        tasks.update(root, tid, evidence={"kind": "note", "ref": f"{provenance}: {reason or _note_title(note_text, note_id)}"})
        out["task_ids"] = [tid]
        return out

    # `task` and `escalate` both file work. Both go through the EXISTING two-stage
    # dedupe (task_dedup): embeddings for recall, a local LLM for the decision,
    # non-destructive merge. A duplicate comes back `merge` and is recorded as the
    # `link` it actually is (guard 3).
    if kind == "task":
        text = str(decision.get("text") or "").strip() or _note_title(note_text, note_id)
    else:
        q = str(decision.get("question") or "").strip()
        text = q or _note_title(note_text, note_id)

    # The triager wrote this spec, not the operator: a note became a ticket by a
    # model's judgement about what the note meant. Recorded so a run that fails on
    # a bad spec points at the triager rather than at whoever wrote the note.
    # `draft`: nobody has looked at this. The triager runs on its own and turns a
    # note into a ticket by its own judgement, which is precisely the filing the
    # approval gate exists for. It proposes; the operator approves.
    res = task_dedup.dedup_add(root, text=text, source=src, provenance=provenance,
                               scoped_by="agent:triager", status="draft")
    task = res.get("task") or {}
    tid = task.get("id")
    out["task_ids"] = [tid] if tid else []
    out["dedup"] = {"decision": res.get("decision"), "cosine": res.get("cosine")}
    if res.get("decision") in ("exact", "merge"):
        out["decision"] = "link"
        out["reason"] = reason or f"already queued as {tid}"
        return out

    if kind == "escalate" and tid:
        tasks.update(root, tid, awaiting={
            "ask": str(decision.get("question") or "").strip() or text,
            "kind": "triage-question",
            "why": reason or f"raised by a note in {scope_key}",
            "raised_by": f"agent:{agent_slug}" if agent_slug else "triager",
            "dedupe_key": provenance,
            "context": {"note": provenance},
        })
    return out


# --- the pass ----------------------------------------------------------------

def run_triage(scope_key: str, *, dry_run: bool = False, rules_body: str = "",
               job_id: str | None = None, agent_slug: str | None = None,
               decider=None) -> dict:
    """One triage pass over one scope. Returns a summary of what it did.

    ``dry_run`` decides everything and writes NOTHING: no tasks, no sidecar, no
    watermark advance. ``decider`` swaps the per-note model call (offline probes;
    it must return the same {decision, reason, ...} shape).

    Note files are never opened for writing here, and this function holds no path
    that could. A pass leaves every note byte-identical.
    """
    scope_key = (scope_key or "workshop").strip()
    knowledge._parse_scope(scope_key)
    started = _now_iso()
    state = read_state(scope_key)
    watermark = float(state.get("watermark") or 0.0)

    candidates = due_notes(scope_key)
    considered = candidates[:MAX_NOTES_PER_PASS]
    deferred = len(candidates) - len(considered)

    root = _task_root(scope_key)
    if root is None:
        return {"scope": scope_key, "dry_run": dry_run, "due": len(candidates),
                "decided": 0, "deferred": len(candidates), "counts": {},
                "watermark": watermark, "skipped": "no task store for this scope"}

    rules = parse_rules(rules_body)
    open_tasks = task_dedup._open_tasks(root)
    decide = decider or (lambda note_id, text: _decide_llm(
        scope_key, note_id, text, open_tasks, rules_body))

    counts = {k: 0 for k in DECISIONS}
    filed = 0
    decided_mark = watermark
    records: dict[str, dict] = {}

    for note in considered:
        # The filing cap stops the pass rather than downgrading the rest to
        # `none`: a note we never looked at must stay due, not be recorded as a
        # deliberate no-op.
        if filed >= MAX_TASKS_PER_PASS:
            deferred += 1
            continue
        try:
            text = note["path"].read_text()
        except OSError:
            continue
        d = apply_rules(rules, text) or decide(note["id"], text)
        if dry_run:
            out = {"decision": validate_decision(d.get("decision")),
                   "reason": d.get("reason") or "", "by": d.get("by") or "model",
                   "ts": started, "task_ids": []}
        else:
            out = apply_decision(root, scope_key, note["id"], text, d,
                                 job_id=job_id, agent_slug=agent_slug,
                                 open_tasks=open_tasks)
            if out["task_ids"] and out["decision"] == "task":
                open_tasks = task_dedup._open_tasks(root)
        counts[out["decision"]] += 1
        if out["decision"] in ("task", "escalate"):
            filed += 1
        out["mtime"] = note["mtime"]
        records[note["id"]] = out
        # Commit-only-if-decided: the watermark only ever reaches a note we
        # actually decided. Candidates are oldest-first, so every note left over
        # is newer than this and stays due.
        decided_mark = max(decided_mark, note["mtime"])

    if not dry_run and records:
        state["watermark"] = decided_mark
        state["updated"] = started
        state["notes"].update(records)
        _write_state(state)

    return {
        "scope": scope_key,
        "dry_run": dry_run,
        "due": len(candidates),
        "decided": len(records),
        "deferred": deferred,
        "counts": counts,
        "rules": len(rules),
        "watermark": decided_mark if not dry_run else watermark,
    }


# =============================================================================
# The QUEUE triager (autonomy-implementation 2026-08-13 §5.2/§5.3)
# =============================================================================
#
# A different role from everything above, in the same module because it shares
# the admission and the cap discipline and nothing else. The note triager reads
# notes and writes tickets. This reads TICKETS and writes to the same store it
# read, which is the whole reason it cannot borrow the mtime watermark above:
# `tasks.update()` stamps `updated` on every write, so a frontier keyed on it
# re-selects every ticket the triager itself just wrote to, forever. The
# frontier here is `tasks.triage_due` (a content hash over title/body/
# depends_on/effort only) and the terminator is the mark, written on EVERY
# decided path including the ones that change nothing.
#
# It does not judge the ticket itself. `ticket_scope_review.review()` is the
# engine (§5.1); this is the runner that selects, caps, writes and stops.

QUEUE_DECISIONS = ("ok", "split", "underspecified", "none")

# Three caps, and the two write caps are the ones that matter. 25 of the 34 open
# tickets fail the acceptance check, so an uncapped first pass files roughly 25
# newly blocked tickets and 25 questions in one morning. That is not a review,
# it is a wall, and the operator would answer none of it.
MAX_QUEUE_TICKETS_PER_PASS = 20     # how many to LOOK at
MAX_SPLIT_PER_PASS = 5              # how many may file children
MAX_UNDERSPECIFIED_PER_PASS = 5     # how many may raise a question

# The exact string `routes/tasks_all.py` keys the attempt-refund suppression on
# (§5.5): a triager-raised block was never a claimed run, so answering it must
# not hand the ticket back an attempt it never spent. Spelled once.
QUEUE_RAISED_BY = "agent:queue-triager"

# Which readiness gap to ask about when several are open, worst first. A worker
# gets the ticket text and nothing else, so the order is "how much of a run does
# this cost if it is missing": a dangling dependency means it cannot start, no
# acceptance means it cannot tell when it is done, no boundaries means the
# touches gate blocks whatever it writes.
_CHECK_PRIORITY = ("title", "dependencies", "acceptance", "boundaries",
                   "entry_points", "objective")

# What an answer would let the worker DO. The operator is being asked to spend
# attention, so the record says what the attention buys.
_CHECK_UNBLOCKS = {
    "title": "the ticket can be identified at all",
    "dependencies": "the dependency resolves and the ticket can become ready",
    "acceptance": "the worker can tell when it is finished and the reviewer can judge it",
    "boundaries": "the worker knows which files it may change, so the touches gate can pass",
    "entry_points": "the worker can start without searching the tree first",
    "objective": "the worker knows which outcome it is aiming at",
    "scope": "the review's objection is settled and the ticket can go to a worker",
}

# The question to ask when the engine named no blocker that matches the gap. One
# question, phrased so an inbox answer is usable as-is.
_CHECK_FALLBACK_ASK = {
    "title": "What should this ticket be called?",
    "dependencies": "Which ticket does this actually depend on?",
    "acceptance": "What does done look like for this ticket?",
    "boundaries": "Which files may this ticket change?",
    "entry_points": "Which file or command should the worker start from?",
    "objective": "What is this ticket trying to achieve?",
    "scope": "What does this ticket need before a worker can start?",
}

# Used to pick WHICH of the engine's blockers answers the chosen gap.
_CHECK_KEYWORDS = {
    "dependencies": ("depend", "blocked by", "prerequisite", "must land", "first"),
    "acceptance": ("done", "acceptance", "accept", "success", "pass", "verify", "test"),
    "boundaries": ("touch", "file", "scope", "boundary", "boundaries", "change"),
    "entry_points": ("where", "which file", "start", "entry", "path", "module"),
    "objective": ("unclear", "not say", "does not say", "ambiguous", "goal", "objective"),
    "title": ("title", "name"),
}

_SENTENCE_END = re.compile(r"(?<=[.!])\s+")


def _review_ticket(task: dict) -> dict:
    """One engine call for one ticket, synchronously.

    `review()` is async only because it pushes the blocking model call onto a
    thread; there is no event loop in this runner and no route calls it, so
    `asyncio.run` is the honest bridge rather than making the whole pass async.
    """
    import asyncio

    from services import ticket_scope_review
    return asyncio.run(ticket_scope_review.review(task))


def _worst_gap(task: dict, known_ids: set[str]) -> tuple[str, str] | None:
    """The highest-cost failing readiness check as ``(check, why)``, or None when
    the ticket passes every one of them."""
    from services import ticket_readiness

    missing = {m["check"]: m["why"]
               for m in ticket_readiness.check(task, known_ids=known_ids)["missing"]}
    for name in _CHECK_PRIORITY:
        if name in missing:
            return name, missing[name]
    return None


def _one_question(blockers: list[str], check: str) -> str:
    """ONE question, out of the engine's LIST of blockers.

    The engine happily returns eight of them. Eight questions in one inbox card
    is not a question, it is a form, and the operator answers none of it. So:
    take the blocker that speaks to the worst readiness gap, discard the rest,
    and keep at most one question mark. A blocker phrased as a statement (the
    common case) keeps its first sentence as context and borrows the gap's own
    question, which is why a compound blocker still yields a single ask.
    """
    fallback = _CHECK_FALLBACK_ASK.get(check) or _CHECK_FALLBACK_ASK["scope"]
    words = _CHECK_KEYWORDS.get(check, ())
    chosen = ""
    for b in blockers:
        if any(w in b.lower() for w in words):
            chosen = b
            break
    if not chosen and blockers:
        chosen = blockers[0]
    if not chosen:
        return fallback
    s = " ".join(chosen.split())
    if "?" in s:
        return s[: s.index("?") + 1][:400]
    head = _SENTENCE_END.split(s)[0].rstrip(". ")
    return f"{head}. {fallback}"[:400]


def _awaiting_record(scope_key: str, check: str, ask: str, why: str) -> dict:
    """The block the queue triager raises. `kind` is EXPLICIT: `_normalize_
    awaiting` defaults it to `input`, and `input` is an expiring kind, so a
    record that leaned on the default would be auto-resolvable rather than
    waiting for the answer it asked for."""
    return {
        "kind": "missing-info",
        "ask": ask,
        "why": why,
        "unblocks_if": _CHECK_UNBLOCKS.get(check) or _CHECK_UNBLOCKS["scope"],
        "raised_by": QUEUE_RAISED_BY,
        # Groups a mass-block: ten tickets missing acceptance share one key and
        # are answerable as one gesture rather than ten.
        "dedupe_key": f"triage:{scope_key}:{check}",
    }


def run_queue_triage(scope_key: str, *, dry_run: bool = True,
                     reviewer=None) -> dict:
    """One queue-triage pass over one scope's `todo` tickets.

    ``dry_run`` defaults to TRUE, the opposite of `run_triage`. This role writes
    into the operator's queue, the trigger is manual on purpose (§5.6), and the
    plan is to read a week of previews before it is ever fired automatically. A
    pass that files by accident is the failure worth defaulting against.

    ``reviewer`` swaps the engine call for offline probes; it must return the
    same `{verdict, reason, subtickets, blockers, stages}` shape.

    Termination, in one place so it can be checked: selection is
    ``status == "todo" and tasks.triage_due(t)``, and every ticket this pass
    DECIDES gets the mark. A ticket it skipped (a cap, an engine failure, an
    unanswered question already on it) gets no mark and stays due, which is the
    same commit-only-if-decided rule the note pass uses for its watermark.
    """
    scope_key = (scope_key or "workshop").strip()
    started = _now_iso()
    root = _task_root(scope_key)
    summary = {
        "scope": scope_key, "mode": "queue", "dry_run": dry_run, "at": started,
        "due": 0, "looked_at": 0, "decided": 0, "deferred": 0,
        "capped": 0, "already_blocked": 0,
        "counts": {k: 0 for k in QUEUE_DECISIONS},
        "decisions": [], "errors": [],
    }
    if root is None:
        summary["skipped"] = "no task store for this scope"
        return summary

    all_tasks = tasks.list_tasks(root)
    known_ids = {t["id"] for t in all_tasks}
    candidates = [t for t in all_tasks
                  if t.get("status") == "todo" and tasks.triage_due(t)]
    # Oldest first, so a capped pass always makes the same kind of progress and
    # a ticket can never be starved by newer arrivals.
    candidates.sort(key=lambda t: (str(t.get("created") or ""), t["id"]))
    looked_at = candidates[:MAX_QUEUE_TICKETS_PER_PASS]
    summary["due"] = len(candidates)
    summary["looked_at"] = len(looked_at)
    summary["deferred"] = len(candidates) - len(looked_at)

    review = reviewer or _review_ticket
    split_filed = 0
    asked = 0

    for task in looked_at:
        tid = task["id"]
        title = str(task.get("title") or "")
        rec = {"id": tid, "title": title[:100]}

        # Already waiting on a person. A second question would overwrite the
        # first one, so this is a no-op with no mark: when the answer lands in
        # the body (which is where an answer has to go to reach the next
        # attempt) the spec hash moves and the ticket comes back due.
        block = task.get("awaiting") or {}
        if block and not block.get("dismissed"):
            summary["already_blocked"] += 1
            continue

        try:
            out = review(task)
        except Exception as e:  # the engine is a model call; it can just fail
            # No mark. An unanswered ticket must stay due, not be recorded as a
            # deliberate no-op because the model was down.
            summary["errors"].append({"id": tid, "error": str(e)[:200]})
            continue

        verdict = str(out.get("verdict") or "ok")
        reason = str(out.get("reason") or "").strip()
        subs = list(out.get("subtickets") or [])
        blockers = [str(b) for b in (out.get("blockers") or []) if str(b).strip()]

        # A "split" the engine already emptied (fewer than two children, or a
        # ticket that has a parent) never reaches here as a split; this is the
        # belt to that braces.
        if verdict == "split" and len(subs) < 2:
            verdict, subs = "ok", []

        # Write caps. A capped ticket is SKIPPED, not downgraded: recording
        # `none` on a split we declined to file would lose the split for good.
        if verdict == "split" and split_filed >= MAX_SPLIT_PER_PASS:
            summary["capped"] += 1
            rec.update(decision="deferred", reason="split cap reached this pass")
            summary["decisions"].append(rec)
            continue
        if verdict == "underspecified" and asked >= MAX_UNDERSPECIFIED_PER_PASS:
            summary["capped"] += 1
            rec.update(decision="deferred", reason="question cap reached this pass")
            summary["decisions"].append(rec)
            continue

        awaiting = None
        if verdict == "underspecified":
            gap = _worst_gap(task, known_ids)
            if gap is None and not blockers:
                # The review objected and then named nothing anyone can answer.
                # This is the one place `none` comes from: looked at, nothing to
                # do. See ticket_scope_review's vocabulary note.
                verdict = "none"
                reason = reason or "the review raised no answerable question"
            else:
                check, why = gap if gap else ("scope", reason or "the review objected to the scope")
                awaiting = _awaiting_record(
                    scope_key, check, _one_question(blockers, check), why)
                rec["check"] = check
                rec["ask"] = awaiting["ask"]

        # THE MARK IS WRITTEN ON EVERY DECIDED PATH, including `ok` and `none`,
        # which change nothing else. That is what makes the pass terminate.
        mark = {"decision": verdict, "spec_hash": tasks.spec_hash(task),
                "reason": reason or None}

        child_ids: list[str] = []
        if not dry_run:
            if awaiting is not None:
                tasks.update(root, tid, awaiting=awaiting)
            if verdict == "split":
                for sub in subs:
                    child = tasks.add(
                        root, title=sub["title"], body=sub.get("body") or "",
                        status="draft", parent=tid,
                        # The request still traces to wherever the parent came
                        # from; only the SPEC of the child was written here.
                        source=task.get("source") or None,
                        scoped_by=QUEUE_RAISED_BY,
                    )
                    child_ids.append(child["id"])
                    known_ids.add(child["id"])
                tasks.update(root, tid, evidence={
                    "kind": "split",
                    "ref": f"queue triage proposed {len(child_ids)} sub-tickets: "
                           + ", ".join(child_ids),
                })
            tasks.update(root, tid, triage=mark)

        if verdict == "split":
            split_filed += 1
            rec["subtickets"] = [s["title"] for s in subs]
            if child_ids:
                rec["child_ids"] = child_ids
        elif verdict == "underspecified":
            asked += 1

        rec["decision"] = verdict
        rec["reason"] = reason
        summary["counts"][verdict] = summary["counts"].get(verdict, 0) + 1
        summary["decided"] += 1
        summary["decisions"].append(rec)

    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="services.triager")
    ap.add_argument("scope", nargs="?", default="workshop")
    ap.add_argument("--queue", action="store_true",
                    help="triage the ticket QUEUE (scope review) instead of notes")
    ap.add_argument("--agent", default=None, help="agent slug whose body carries the rules")
    ap.add_argument("--job", default=None, help="job id to stamp on filed tasks (source.ref)")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--apply", action="store_true", help="run for real and write")
    grp.add_argument("--dry-run", action="store_true", help="preview only (the default)")
    args = ap.parse_args()

    if args.queue:
        # The queue pass defaults to a preview even without --dry-run: the
        # trigger is manual on purpose and --apply is the only way to write.
        print(json.dumps(run_queue_triage(
            args.scope, dry_run=not args.apply), indent=2))
        raise SystemExit(0)

    body = ""
    if args.agent:
        from services import agents_registry
        rec = agents_registry.get(args.agent)
        body = (rec or {}).get("definition") or ""
    # Default is a non-mutating preview, matching services.memory_keeper: a bare
    # self-test run must never file a ticket.
    print(json.dumps(run_triage(
        args.scope, dry_run=not args.apply, rules_body=body,
        job_id=args.job, agent_slug=args.agent,
    ), indent=2))
