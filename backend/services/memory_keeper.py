"""Memory-keeper: the consolidation organ (orchestration-and-autonomy Phase O5).

A pure-additive organ that runs OFF the hot path (between sessions) to fold a
scope's recent episodic material into its long-term semantic and procedural
stores. It never runs inside a chat turn, never touches a live prompt cache, and
never races the primary loop: it reads the append-only Job log and writes files.

The write path is the mem0/ACE shape the design (Section 6) settled on:

  Gather  new episodic material since a per-scope watermark (recently-closed
          Jobs for the scope, new learnings, any chat-distilled facts).
  Route   each candidate fact with an ADD / UPDATE / RETIRE / NOOP decision
          against the top-k similar existing notes. A DETERMINISTIC router is the
          reliable core (jaccard near-duplicate => UPDATE or NOOP; a negation /
          supersession cue against a similar prior fact => RETIRE-as-supersede,
          Zep style; otherwise ADD). A single `claude -p` call OPTIONALLY refines
          the routing, and degrades to the deterministic decision on any error so
          the pass never hard-fails offline.
  Apply   itemized deltas only (ACE context-collapse guard): every write is a
          single bulleted add/update/retire line. Semantic facts append to the
          scope knowledge note (a superseded fact is annotated "was X, now Y",
          never deleted); procedural insights route to learnings.py.
  Promote a procedure to a proposed skill when the same pattern recurs (>= 3
          times) in the scope's learnings, recording a skill-proposal note for
          the operator to confirm. Skill CREATION stays operator-confirmed: the
          keeper drafts, it never auto-activates a skill.
  Cap     the scope MEMORY.md index (Claude Code 200-line rule) via
          knowledge.enforce_index_cap.
  Advance the watermark on success.

Public entrypoint: ``run_consolidation(scope_key) -> dict`` (a summary of counts).
Invoked off the hot path by scripts/memory_keeper_run.sh (or ``python -m
services.memory_keeper <scope>``). It runs to completion with no network (the
deterministic router) and does not crash on an empty scope.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

from services import jobs, knowledge, learnings

# The single per-scope note that holds consolidated semantic facts. Every write is
# an appended bullet (itemized delta); existing bullets are never rewritten.
CONSOLIDATED_NOTE = "consolidated-memory"
_CONSOLIDATED_HEADER = (
    "# Consolidated memory\n\n"
    "Semantic facts folded in by the memory-keeper as itemized deltas (never a "
    "monolithic rewrite). A superseded fact is annotated in place, not deleted.\n\n"
)

# A note the compaction path may drop chat-distilled facts into; folded in if present.
DISTILLED_NOTE = "chat-distilled"

# Near-duplicate threshold on canonical token-set jaccard (matches learnings).
_NEAR_DUP_JACCARD = learnings._NEAR_DUP_JACCARD
# A candidate carrying a supersession cue only RETIRES a prior fact it is actually
# about; this is the low similarity floor for "about the same thing".
_SUPERSEDE_SIM_FLOOR = 0.30
# Top-k similar notes pulled from the scope for routing context.
_TOPK = 5
# How many times a procedural pattern must recur before it is proposed as a skill.
_SKILL_RECUR_MIN = 3
# How many recent Jobs to scan per scope (the corpus is single-operator small).
_JOBS_SCAN_LIMIT = 1000

# Negation / supersession cues that mark a candidate as correcting a prior fact.
_SUPERSEDE_RE = re.compile(
    r"\b(no longer|instead of|not\s|used to|supersed|replaces?|replaced|"
    r"deprecat|obsolete|was wrong|actually|now use|switched to|renamed)\b",
    re.IGNORECASE,
)

# --- selectivity: keep heartbeat/status chatter OUT of long-term memory -------
# Only substantive material becomes memory. The single-operator Job log is full
# of overseer/masini heartbeat ticks and per-run status narration; folding those
# in produces garbage semantic "facts". These filters drop that noise at gather.

# Generic prose shorter than this is treated as status narration, not a fact.
_MIN_FACT_LEN = 40

# Status-narration phrases: a candidate carrying one is per-tick chatter, not a
# durable fact, and is dropped (NOOP).
_NARRATION_RE = re.compile(
    r"(checking open tasks|no new\b|proposing a task|monitoring|"
    r"reducing (the )?(backlog|blocked|todo)|heartbeat|preparing to dispatch|"
    r"dispatch(ing|ed)?\b|no (immediate )?action|nothing to do|top open tasks|"
    r"status update|per[- ]tick|\btick\b)",
    re.IGNORECASE,
)

# Vacuous/negative failure reasons that carry no actionable lesson.
_VACUOUS_RE = re.compile(
    r"(without a recorded reason|no recorded reason|^job failed\.?$|^failed\.?$|"
    r"unknown( reason)?|no reason|^n/?a$)",
    re.IGNORECASE,
)

# Jobs bound to these sources are heartbeat/coordination chatter, never work.
_NOISE_AGENT_RE = re.compile(
    r"(overseer|masini|heartbeat|supervisor|per[- ]channel|discord)",
    re.IGNORECASE,
)


def _is_narration(text: str) -> bool:
    """True if the text is per-tick status narration (or too short/generic to be
    a durable fact) and must not become memory."""
    t = (text or "").strip()
    if len(t) < _MIN_FACT_LEN:
        return True
    return bool(_NARRATION_RE.search(t))


def _is_vacuous(text: str) -> bool:
    """True if a failure reason carries no actionable content."""
    t = (text or "").strip()
    return (not t) or bool(_VACUOUS_RE.search(t))


def _is_noise_job(rec: dict) -> bool:
    """True for a Job that is heartbeat/coordination chatter, not real work: an
    overseer/masini/supervisor binding, or a per-channel (chat-bound) tick."""
    if _NOISE_AGENT_RE.search(rec.get("agent_id") or ""):
        return True
    if rec.get("chat_id"):
        return True
    return False


def _is_work_job(rec: dict) -> bool:
    """True for a Job that represents real WORK worth learning from: a child Job
    attempt against a task, or any Job carrying a structured receipt. A bare
    coordinator sweep (no task, no receipt) is not itself work."""
    if rec.get("task_id"):
        return True
    ev = rec.get("evidence")
    return isinstance(ev, dict) and ev.get("kind") == "receipt"


# --- roots + watermark -------------------------------------------------------
def _state_dir() -> Path:
    # No eager mkdir: a dry-run reads the watermark/proposed state and must not
    # create anything. Writers (_write_watermark / _save_proposed) create the dir.
    return knowledge._runtime() / "memory-keeper"


def _safe_scope(scope_key: str) -> str:
    return scope_key.replace(":", "-")


def _watermark_path(scope_key: str) -> Path:
    return _state_dir() / f"{_safe_scope(scope_key)}.watermark"


def _proposed_path(scope_key: str) -> Path:
    return _state_dir() / f"{_safe_scope(scope_key)}.proposed.json"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _read_watermark(scope_key: str) -> str:
    p = _watermark_path(scope_key)
    if not p.exists():
        return ""  # empty scope / first run: an empty string sorts before any iso
    return p.read_text().strip()


def _write_watermark(scope_key: str, value: str) -> None:
    p = _watermark_path(scope_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(value + "\n")
    os.replace(tmp, p)


def _load_proposed(scope_key: str) -> set[str]:
    p = _proposed_path(scope_key)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    ids = data.get("ids") if isinstance(data, dict) else None
    return {str(x) for x in ids} if isinstance(ids, list) else set()


def _save_proposed(scope_key: str, ids: set[str]) -> None:
    p = _proposed_path(scope_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"ids": sorted(ids)}, indent=2) + "\n")
    os.replace(tmp, p)


# --- async bridge (off the hot path; no running loop to nest into) -----------
def _run(coro):
    return asyncio.run(coro)


# --- gather ------------------------------------------------------------------
def _scope_jobs(scope_key: str, since_iso: str) -> list[dict]:
    """Recently-closed Jobs for the scope that finished after the watermark."""
    kind, slug = knowledge._parse_scope(scope_key)
    if kind == "project":
        recs = jobs.read(project_slug=slug, limit=_JOBS_SCAN_LIMIT)
    elif kind == "agent":
        recs = jobs.read(agent_id=f"agent:{slug}", limit=_JOBS_SCAN_LIMIT)
        if not recs:
            recs = jobs.read(agent_id=slug, limit=_JOBS_SCAN_LIMIT)
    elif kind == "app":
        recs = jobs.read(project_slug=slug, limit=_JOBS_SCAN_LIMIT)
    else:  # workshop: the `self` scope, jobs with no other binding
        recs = [
            r for r in jobs.read(limit=_JOBS_SCAN_LIMIT)
            if (r.get("project_slug") in (None, "", "self", "workshop"))
            and not (r.get("agent_id") or "").startswith("agent:")
        ]
    out = []
    for r in recs:
        if r.get("status") not in ("done", "failed"):
            continue
        ended = r.get("ended_at") or ""
        if since_iso and ended <= since_iso:
            continue
        out.append(r)
    return out


def _job_failure_reason(rec: dict) -> str:
    """The concrete reason a work Job failed, from its receipt evidence (the
    reviewer's `design` verdict, else the run summary), or a plain string."""
    ev = rec.get("evidence")
    if isinstance(ev, dict):
        for key in ("design", "summary"):
            v = str(ev.get(key) or "").strip()
            if v:
                return v[:300]
    elif isinstance(ev, str):
        return ev.strip()[:300]
    return ""


def _gather(scope_key: str, since_iso: str) -> dict:
    """Return {'semantic': [str], 'procedural': [str], 'counts': {...}}.

    Selective by construction: heartbeat/coordination Jobs are excluded, only
    real-work Jobs are considered, Job summary prose is NEVER folded as a semantic
    fact (it is per-run status narration), and a procedural lesson is taken only
    from a concrete, non-vacuous failure reason. Semantic facts come from the
    curated chat-distilled note (narration-filtered), plus operator corrections
    surfaced through learnings; not from per-tick status text."""
    semantic: list[str] = []
    procedural: list[str] = []
    job_n = 0
    work_n = 0
    for rec in _scope_jobs(scope_key, since_iso):
        if _is_noise_job(rec):
            continue
        job_n += 1
        if not _is_work_job(rec):
            continue
        work_n += 1
        if rec.get("status") == "failed":
            reason = _job_failure_reason(rec)
            if reason and not _is_vacuous(reason) and not _is_narration(reason):
                procedural.append(f"failure pattern: {reason}")

    # Semantic facts come only from the curated chat-distilled note, if present,
    # with narration/short-prose dropped.
    distilled_n = 0
    body = knowledge.get_note(scope_key, DISTILLED_NOTE)
    if body:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("- ") and len(line) > 4:
                fact = line[2:].strip()
                if not _is_narration(fact):
                    semantic.append(fact)
                    distilled_n += 1

    # New learnings since the watermark already live in the store; they drive
    # skill promotion (below), not re-routed writes. Counted for the summary.
    learn_scope = _learnings_scope(scope_key)
    learn_n = 0
    for e in _safe_list_learnings(learn_scope):
        if not since_iso or (e.get("created_at") or "") > since_iso:
            learn_n += 1

    return {
        "semantic": semantic,
        "procedural": procedural,
        "counts": {"jobs": job_n, "work": work_n, "distilled": distilled_n, "learnings": learn_n},
    }


def _learnings_scope(scope_key: str) -> str:
    """Map a knowledge scope key to the learnings scope key. They share the same
    shapes; `workshop` is valid in both."""
    return scope_key


def _safe_list_learnings(learn_scope: str) -> list[dict]:
    try:
        return learnings.list_learnings(learn_scope)
    except Exception:
        return []


# --- routing (deterministic core, optional claude refine) --------------------
def _note_bullets(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- ") and len(line) > 2:
            out.append(line[2:].strip())
    return out


def _route_semantic(candidate: str, existing: list[str]) -> tuple[str, str | None]:
    """Deterministic ADD / UPDATE / RETIRE / NOOP against existing bullets.

    Returns (decision, matched_bullet). NOOP on an exact canonical duplicate;
    RETIRE (supersede) when the candidate carries a negation/supersession cue and
    is about a similar prior fact; UPDATE on a near-duplicate; ADD otherwise."""
    cand_tokens = learnings._token_set(candidate)
    cand_canon = learnings._canonical(candidate)
    best_sim = 0.0
    best_bullet: str | None = None
    for b in existing:
        if learnings._canonical(b) == cand_canon:
            return ("noop", b)
        sim = learnings._jaccard(cand_tokens, learnings._token_set(b))
        if sim > best_sim:
            best_sim = sim
            best_bullet = b
    if _SUPERSEDE_RE.search(candidate) and best_bullet is not None and best_sim >= _SUPERSEDE_SIM_FLOOR:
        return ("retire", best_bullet)
    if best_sim >= _NEAR_DUP_JACCARD:
        return ("update", best_bullet)
    return ("add", None)


def _delta_line(decision: str, candidate: str, matched: str | None) -> str | None:
    """The single bulleted line for an itemized delta (or None for a NOOP)."""
    if decision == "add":
        return f"- {candidate}"
    if decision == "update":
        return f"- (updated) {candidate}"
    if decision == "retire":
        old = (matched or "")[:200]
        return f"- (superseded) was: {old} -> now: {candidate}"
    return None


def _maybe_refine(scope_key: str, items: list[dict]) -> bool:
    """Optionally refine the deterministic decisions with a single `claude -p`
    (haiku) call. Off by default (MEMORY_KEEPER_REFINE=1 opts in) so the offline
    self-run stays deterministic and fast. Mutates each item's `decision` in place
    and returns True if the model was consulted. Degrades to the deterministic
    decisions on any import/CLI/parse error, so the pass never hard-fails."""
    if not items or not os.environ.get("MEMORY_KEEPER_REFINE"):
        return False
    try:
        from services import llm_router
    except Exception:
        return False
    listing = "\n".join(
        f"{i}. decision={it['decision']} :: {it['candidate'][:200]}"
        for i, it in enumerate(items)
    )
    prompt = (
        "You are refining a memory-consolidation router for scope "
        f"{scope_key}. For each candidate fact below, a deterministic router "
        "already chose ADD, UPDATE, RETIRE, or NOOP. Confirm or correct each. "
        "ADD a genuinely new durable fact; UPDATE a refinement of an existing "
        "one; RETIRE a fact that supersedes/contradicts a prior one; NOOP noise "
        "or a duplicate. Reply with ONLY a JSON object mapping the index (as a "
        "string) to one of add, update, retire, noop.\n\n" + listing
    )
    try:
        res = llm_router.call_cloud(
            prompt, model=llm_router.TIER_HAIKU, tools_disabled=True,
            max_turns=1, timeout_s=90,
        )
    except Exception:
        return False
    text = (res or {}).get("text") or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return False
    try:
        mapping = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return False
    if not isinstance(mapping, dict):
        return False
    for i, it in enumerate(items):
        choice = str(mapping.get(str(i), "")).strip().lower()
        if choice in ("add", "update", "retire", "noop"):
            # The model cannot invent a match target; only an ADD/UPDATE swap or a
            # downgrade to NOOP is honored, keeping the retire target deterministic.
            if choice == "retire" and it.get("matched") is None:
                continue
            it["decision"] = choice
    return True


# --- apply -------------------------------------------------------------------
def _apply_semantic(scope_key: str, candidates: list[str], *, dry_run: bool) -> dict:
    """Route + apply the semantic candidates as itemized deltas into the scope's
    consolidated note. One read, one append-only write (existing bullets kept
    verbatim), so it is an itemized delta and never a monolithic rewrite. When
    dry_run is True it routes and counts but writes nothing."""
    counts = {"add": 0, "update": 0, "retire": 0, "noop": 0}
    if not candidates:
        return counts

    body = knowledge.get_note(scope_key, CONSOLIDATED_NOTE) or _CONSOLIDATED_HEADER
    existing = _note_bullets(body)

    items: list[dict] = []
    for cand in candidates:
        cand = (cand or "").strip()
        if not cand:
            continue
        decision, matched = _route_semantic(cand, existing)
        items.append({"candidate": cand, "decision": decision, "matched": matched})
        # Fold the just-decided fact into the working set so a later candidate in
        # the same pass dedupes against it (avoids adding two near-dupes at once).
        if decision in ("add", "update", "retire"):
            existing.append(cand)

    _maybe_refine(scope_key, items)

    new_lines: list[str] = []
    for it in items:
        counts[it["decision"]] += 1
        line = _delta_line(it["decision"], it["candidate"], it["matched"])
        if line:
            new_lines.append(line)

    if new_lines and not dry_run:
        if not body.endswith("\n"):
            body += "\n"
        body += "".join(l + "\n" for l in new_lines)
        _run(knowledge.put_note(
            scope_key, CONSOLIDATED_NOTE, "Consolidated memory", body,
            hook="memory-keeper consolidated facts",
        ))
    return counts


def _apply_procedural(scope_key: str, candidates: list[str], *, dry_run: bool) -> dict:
    """Route procedural insights to learnings.py, which does its own jaccard
    dedup (a duplicate attaches provenance rather than dropping the signal). When
    dry_run is True it simulates that dedup (same canonical-hash + jaccard test)
    to report add/noop without recording anything."""
    counts = {"add": 0, "noop": 0}
    learn_scope = _learnings_scope(scope_key)

    if dry_run:
        existing = _safe_list_learnings(learn_scope)
        hashes = {learnings._canon_hash(e.get("text", "")) for e in existing}
        tokensets = [learnings._token_set(e.get("text", "")) for e in existing]
        for cand in candidates:
            cand = (cand or "").strip()
            if not cand:
                continue
            h = learnings._canon_hash(cand)
            ts = learnings._token_set(cand)
            dup = h in hashes or any(
                learnings._jaccard(ts, x) >= _NEAR_DUP_JACCARD for x in tokensets)
            if dup:
                counts["noop"] += 1
            else:
                counts["add"] += 1
                hashes.add(h)
                tokensets.append(ts)
        return counts

    for cand in candidates:
        cand = (cand or "").strip()
        if not cand:
            continue
        try:
            res = learnings.record(
                cand, source="memory-keeper", scope=learn_scope, tags=["consolidated"],
            )
        except Exception:
            continue
        if res.get("recorded"):
            counts["add"] += 1
        else:
            counts["noop"] += 1
    return counts


# --- skill promotion (drafted, operator-confirmed) ---------------------------
def _slugify(text: str, *, max_words: int = 5) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    slug = "-".join(words[:max_words]) or "pattern"
    return slug[:60].strip("-") or "pattern"


def _draft_skill_body(lessons: list[str]) -> str:
    """Assemble a proposed SKILL.md body deterministically (no model call), the
    same shape learnings.distill_skill would use. We draft rather than call
    distill_skill because that writes a live skill; skill creation is operator-
    confirmed, so the keeper only files a proposal."""
    preamble = ("Proposed skill drafted by the memory-keeper from a repeated "
                "procedural pattern. Confirm to activate; edit freely first.")
    if lessons:
        body = preamble + "\n\nLearned patterns to apply:\n" + "\n".join(f"- {t}" for t in lessons)
    else:
        body = preamble
    return body


def _promote_skills(scope_key: str, *, dry_run: bool) -> int:
    """Propose a skill for each procedural pattern that recurs >= _SKILL_RECUR_MIN
    times in the scope's learnings and has not been proposed before. Records a
    skill-proposal note per pattern (operator confirms creation) and returns the
    count proposed this pass. Only a repeated, specific, actionable procedure
    qualifies: vacuous/negative or narration patterns are excluded. When dry_run
    is True it counts what WOULD be proposed and writes nothing."""
    learn_scope = _learnings_scope(scope_key)
    entries = _safe_list_learnings(learn_scope)
    if not entries:
        return 0
    proposed = _load_proposed(scope_key)
    made = 0
    for e in entries:
        eid = str(e.get("id") or "")
        if not eid or eid in proposed:
            continue
        # A learning whose provenance recorded it >= N times is a recurring pattern
        # (the dedup path attaches each recurrence to the existing entry).
        recur = len(e.get("provenance") or []) or 1
        if recur < _SKILL_RECUR_MIN:
            continue
        text = (e.get("text") or "").strip()
        # Raise the bar: a skill must come from a specific, actionable procedure,
        # never a vacuous failure ("failed without a recorded reason") or per-tick
        # status narration.
        if not text or _is_vacuous(text) or _is_narration(text):
            continue
        name = f"proposed-{_slugify(text)}"
        note_id = f"skill-proposal-{eid}"
        if not dry_run:
            proposal = (
                f"# Skill proposal: {name}\n\n"
                f"Recurred {recur} times in the {learn_scope} learnings. "
                "Operator: confirm to create this skill (it is NOT active yet).\n\n"
                f"Proposed name: {name}\n\n"
                f"Description: recurring procedure distilled from operator/reviewer feedback.\n\n"
                "Proposed SKILL.md body:\n\n"
                + _draft_skill_body([text])
                + "\n"
            )
            try:
                _run(knowledge.put_note(
                    scope_key, note_id, f"Skill proposal: {name}", proposal,
                    hook=f"proposed skill (recurred {recur}x), operator-confirmed",
                ))
            except Exception:
                continue
            proposed.add(eid)
        made += 1
    if made and not dry_run:
        _save_proposed(scope_key, proposed)
    return made


# --- public entrypoint -------------------------------------------------------
def run_consolidation(scope_key: str, dry_run: bool = False) -> dict:
    """Run one consolidation pass for a scope, off the hot path. Returns a summary
    of what it did (or would do). Safe on an empty scope (returns zeros) and
    offline (the deterministic router).

    When ``dry_run`` is True this is a pure PREVIEW: it does the full gather +
    route + counts but performs NO writes at all (no note writes, no
    learnings.record, no skill-proposal files, no index-cap write, no watermark
    advance), so a verification/preview run never mutates the tree. When False it
    applies the itemized deltas, enforces the index cap, and advances the
    watermark on success."""
    scope_key = (scope_key or "workshop").strip()
    # Validate the scope shape early (raises on junk); `workshop` is allowed.
    knowledge._parse_scope(scope_key)

    started = _now_iso()
    since = _read_watermark(scope_key)

    gathered = _gather(scope_key, since)
    semantic_counts = _apply_semantic(scope_key, gathered["semantic"], dry_run=dry_run)
    procedural_counts = _apply_procedural(scope_key, gathered["procedural"], dry_run=dry_run)
    skills_proposed = _promote_skills(scope_key, dry_run=dry_run)

    demoted = 0
    if not dry_run:
        try:
            demoted = int(knowledge.enforce_index_cap(scope_key).get("demoted", 0))
        except Exception:
            demoted = 0
        # Advance the watermark to this pass's start so the next pass only sees
        # newer Jobs (a Job finishing mid-pass is picked up next time, not skipped).
        _write_watermark(scope_key, started)

    return {
        "scope": scope_key,
        "dry_run": dry_run,
        "gathered": gathered["counts"],
        "add": semantic_counts["add"] + procedural_counts["add"],
        "update": semantic_counts["update"],
        "retire": semantic_counts["retire"],
        "noop": semantic_counts["noop"] + procedural_counts["noop"],
        "semantic": semantic_counts,
        "procedural": procedural_counts,
        "skills_proposed": skills_proposed,
        "index_demotions": demoted,
        "router": "deterministic",
        "watermark": started if not dry_run else since,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="services.memory_keeper")
    ap.add_argument("scope", nargs="?", default="workshop")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--apply", action="store_true",
                     help="run for real and write (dispatcher/scripts use this)")
    grp.add_argument("--dry-run", action="store_true",
                     help="preview only, write nothing (the default)")
    args = ap.parse_args()
    # Default is a non-mutating preview: the bare `python -m services.memory_keeper
    # <scope>` self-test must never pollute the tree. --apply opts into writes.
    print(json.dumps(run_consolidation(args.scope, dry_run=not args.apply), indent=2))
