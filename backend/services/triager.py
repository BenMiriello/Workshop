"""Triager — the note-triage runner (final-plan P4 / roles report 3.3 + 4.5).

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
    return knowledge._runtime() / "triager"


def _safe_scope(scope_key: str) -> str:
    return scope_key.replace(":", "-")


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

  task     — the note describes work that must happen and is not already queued.
  link     — the note restates, refines, or adds evidence to an OPEN task below.
  escalate — the note raises something this scope cannot decide; it needs the
             operator to answer a specific question.
  none     — everything else.

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

    res = task_dedup.dedup_add(root, text=text, source=src, provenance=provenance)
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


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="services.triager")
    ap.add_argument("scope", nargs="?", default="workshop")
    ap.add_argument("--agent", default=None, help="agent slug whose body carries the rules")
    ap.add_argument("--job", default=None, help="job id to stamp on filed tasks (source.ref)")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--apply", action="store_true", help="run for real and write")
    grp.add_argument("--dry-run", action="store_true", help="preview only (the default)")
    args = ap.parse_args()

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
