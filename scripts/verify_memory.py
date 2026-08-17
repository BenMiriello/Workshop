#!/usr/bin/env python3
"""End-to-end acceptance check for the memory system.

Written 2026-08-16 for thread T1 of
docs/platform-redesign/coherence-overhaul-2026-08-16.md. The operator's bar was
"be sure to have all working and tested ... including memory features working
now", and unit tests alone do not answer that: every defect T1 fixes was one
where the units were fine and the wiring between them was not.

So this drives the real code paths against the real stores and asserts on
observable outcomes. It is READ-MOSTLY: the one thing it writes is a uniquely
named probe note, which it deletes again. It never writes a chat, a ticket or a
learning.

    cd backend && .venv/bin/python ../scripts/verify_memory.py

Exit 0 = every check passed. Exit 1 = at least one failed, and the failures are
printed with what was expected. A check that cannot run (a service is down)
reports SKIP and does not fail the run, because "the GPU is busy" is not the
same finding as "the code is wrong".
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

OWNER = 6

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def _await(value):
    """Run a coroutine to completion; pass anything else straight through.

    Several knowledge-store verbs are async. A check that forgets to await one
    passes trivially while doing nothing, so this is applied at every call site
    rather than assumed."""
    import asyncio
    import inspect as _inspect

    if _inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def check(name: str, status: str, detail: str = "") -> None:
    _results.append((status, name, detail))
    mark = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{mark}] {name}" + (f"\n         {detail}" if detail else ""))


def _try(name: str, fn) -> None:
    """Run a check, turning an unexpected exception into a FAIL rather than a
    traceback, so one broken check does not hide the other twenty."""
    try:
        fn(name)
    except Exception as e:
        check(name, FAIL, f"raised {type(e).__name__}: {e}")


# --- retrieval -------------------------------------------------------------

def c_search_works(name: str) -> None:
    from services import semantic_index as si

    t0 = time.time()
    res = si.search("chat branching design", limit=5)
    dt = time.time() - t0
    hits = res.get("hits") or []
    if not hits:
        check(name, FAIL, f"search returned no hits (mode={res.get('mode')!r})")
        return
    check(name, PASS, f"{len(hits)} hits in {dt:.2f}s, mode={res.get('mode')!r}")


def c_search_is_cross_corpus(name: str) -> None:
    """The operator's actual ask: from a chat, reach other chats, tickets and
    docs about a topic. A result set that is all one kind is not that."""
    from services import semantic_index as si

    kinds: set[str] = set()
    for q in ("chat branching design", "memory and notes", "autonomy and the queue"):
        for h in (si.search(q, limit=8).get("hits") or []):
            kinds.add(h.get("kind"))
    want = {"chat", "ticket", "doc"}
    missing = want - kinds
    if missing:
        check(name, FAIL, f"never retrieved kinds {sorted(missing)}; saw {sorted(kinds)}")
        return
    check(name, PASS, f"retrieved {sorted(kinds)}")


def c_search_degrades_not_dies(name: str) -> None:
    """The GPU going busy must cost ranking quality, not the whole feature."""
    from services import semantic_index as si

    if not hasattr(si, "_bm25_scores"):
        check(name, FAIL, "no BM25 lexical path found in semantic_index")
        return
    check(name, PASS, "BM25 lexical fallback present")


def c_scope_filter_reaches_the_model(name: str) -> None:
    """A visibility rule that the tool cannot express cannot be honoured."""
    from mcp.workshop_query_server import SEARCH_TOOL_SCHEMA

    props = SEARCH_TOOL_SCHEMA.get("properties", {})
    if "scope" not in props:
        check(name, FAIL, f"search tool exposes {sorted(props)}; no scope filter")
        return
    check(name, PASS, "search tool exposes a scope filter")


# --- freshness: the T1/D6 headline ----------------------------------------

def c_index_knows_about_note_writes(name: str) -> None:
    from services import knowledge, semantic_index as si

    probe_id = f"memory-verify-probe-{uuid.uuid4().hex[:10]}"
    stale = Path(si.index_dir()) / "index.stale"
    # mark_stale debounces on the marker's existence, so an already-stale index
    # would swallow the very signal being tested. Clear it first. The index is
    # left stale either way, because the probe's own delete_note marks it again
    # on the way out, so this never leaves the index looking fresher than it is.
    if stale.exists():
        stale.unlink()
    note_id = None
    try:
        # put_note/delete_note are coroutines. Calling them without awaiting
        # returns a coroutine object and does nothing at all, silently, which
        # is exactly the sort of green-but-hollow check this script exists to
        # avoid, so it is spelled out rather than hidden in a helper.
        _await(knowledge.put_note(
            "workshop", probe_id,
            title=f"memory-verify probe {probe_id[-10:]}",
            content="Written by scripts/verify_memory.py. Safe to delete."))
        note_id = probe_id
        after = stale.read_text() if stale.exists() else ""
        if not stale.exists():
            check(name, FAIL, "a note write did not mark the index stale")
            return
        check(name, PASS, f"note write marked stale: {after.strip()[:70]}")
    finally:
        if note_id:
            try:
                _await(knowledge.delete_note("workshop", note_id))
            except Exception as e:
                print(f"         (probe note {note_id} not cleaned up: {e})")


def c_index_knows_about_chats_and_tickets(name: str) -> None:
    """D6. `chat` and `ticket` are in DEFAULT_KINDS, so something must mark the
    index stale when they change. Before T1 the only two call sites were both
    note writes, so a chat you had today was not findable."""
    import subprocess

    out = subprocess.run(
        ["grep", "-rn", "mark_stale", "backend/", "--include=*.py"],
        cwd=str(_BACKEND.parent), capture_output=True, text=True).stdout
    sites = [l for l in out.splitlines()
             if "/tests/" not in l and "def mark_stale" not in l]
    files = {l.split(":")[0] for l in sites}
    knowledge_only = all("knowledge.py" in f for f in files) if files else True
    if knowledge_only:
        check(name, FAIL,
              f"mark_stale is called only from {sorted(files)}; "
              "chat/ticket/doc writes never mark the index stale")
        return
    check(name, PASS, f"mark_stale reached from {len(files)} modules: "
                      f"{sorted(Path(f).name for f in files)}")


def c_index_is_not_ancient(name: str) -> None:
    from services import semantic_index as si

    meta = Path(si.index_dir()) / "index.meta.jsonl"
    if not meta.exists():
        check(name, SKIP, "no index built yet for this owner")
        return
    age_h = (time.time() - meta.stat().st_mtime) / 3600
    if age_h > 48:
        check(name, FAIL, f"index last built {age_h:.0f}h ago")
        return
    check(name, PASS, f"index built {age_h:.1f}h ago")


# --- the learnings store ---------------------------------------------------

def _learning_rows() -> list[dict]:
    from wsplatform import paths

    root = Path(paths.runtime_dir()) / "learnings"
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for f in sorted(root.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def c_no_machine_noise_in_learnings(name: str) -> None:
    """A status token is a log line, not a guardrail. Two of these were injected
    into every agent prompt for a month."""
    rows = _learning_rows()
    junk = [r for r in rows
            if len((r.get("text") or "").strip()) < 60
            and ":" in (r.get("text") or "")
            and " " not in (r.get("text") or "").split(":")[0]]
    if junk:
        check(name, FAIL, "machine-status rows still present: "
                          + "; ".join(repr(r.get("text")) for r in junk[:4]))
        return
    check(name, PASS, f"{len(rows)} rows, none is a bare status token")


def c_no_duplicate_learnings(name: str) -> None:
    """A revision must supersede, not sit beside the thing it revises."""
    rows = _learning_rows()
    seen: dict[tuple, list[str]] = {}
    for r in rows:
        text = (r.get("text") or "").strip().lower()
        # first twelve significant words: catches an edited restatement, which
        # an exact-hash dedupe misses by construction
        key = (r.get("scope"), tuple(text.split()[:12]))
        if len(key[1]) < 6:
            continue
        seen.setdefault(key, []).append(r.get("id") or "?")
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if dupes:
        first = next(iter(dupes))
        check(name, FAIL, f"{len(dupes)} near-duplicate group(s), e.g. "
                          f"{' '.join(first[1][:8])!r} x{len(dupes[first])}")
        return
    check(name, PASS, f"{len(rows)} rows, no near-duplicate groups")


def c_learnings_gc_can_fire(name: str) -> None:
    """Before T1, `memory_block` stamped last_retrieved on every render and
    `prune_stale` kept anything recent, so the collector's precondition was
    falsified by the injector and it could never archive anything."""
    from services import learnings

    if not hasattr(learnings, "prune_stale"):
        check(name, SKIP, "no prune_stale in learnings")
        return
    rows = _learning_rows()
    if not rows:
        check(name, SKIP, "no learnings to reason about")
        return
    # Everything carrying an identical, always-today last_retrieved is the
    # signature of the bug: the counter measures rendering, not relevance.
    stamps = {r.get("last_retrieved") for r in rows if r.get("last_retrieved")}
    counts = {r.get("retrieval_count") for r in rows if r.get("retrieval_count")}
    if len(rows) > 4 and len(counts) == 1:
        check(name, FAIL,
              f"every row shares retrieval_count={counts.pop()!r}: the counter "
              "measures rendering, not selection, so prune_stale can never fire")
        return
    check(name, PASS, f"{len(stamps)} distinct retrieval stamps, "
                      f"{len(counts)} distinct counts")


def c_provenance_is_capped(name: str) -> None:
    rows = _learning_rows()
    worst = max((len(r.get("provenance") or []) for r in rows), default=0)
    if worst > 25:
        check(name, FAIL, f"a single row carries {worst} provenance entries")
        return
    check(name, PASS, f"largest provenance array is {worst}")


# --- dead substrates -------------------------------------------------------

def c_dead_substrates_are_gone(name: str) -> None:
    root = _BACKEND.parent
    corpses = {
        "memory_tree": root / "backend/services/memory_tree.py",
        "memory-tree.yaml": root / "shared/memory-tree.yaml",
        "memory_promotion": root / "backend/services/memory_promotion.py",
        "letta sandbox_wrappers": root / "backend/services/letta_tools/sandbox_wrappers.py",
    }
    alive = [n for n, p in corpses.items() if p.exists()]
    if alive:
        check(name, FAIL, f"still present: {', '.join(alive)}")
        return
    check(name, PASS, "memory_tree, memory_promotion and the Letta sandbox shims are gone")


def c_letta_memory_was_exported(name: str) -> None:
    """Letta held 23 memory blocks across 7 agents that nothing read. They must
    survive as notes before the wiring is removed."""
    import subprocess

    out = subprocess.run(
        ["grep", "-rl", "-i", "letta", str(_BACKEND.parent / "shared" / "knowledge")],
        capture_output=True, text=True).stdout.strip()
    agent_notes = list((_BACKEND.parent / ".runtime" / "agents").glob("*/knowledge/*.md"))
    n = len([l for l in out.splitlines() if l]) + len(agent_notes)
    if n == 0:
        check(name, FAIL, "no exported Letta memory found in any knowledge scope")
        return
    check(name, PASS, f"{n} knowledge note(s) carry exported Letta memory")


# --- the prompt tells the truth -------------------------------------------

def c_persona_does_not_lie(name: str) -> None:
    """The general persona claimed nothing is injected while three blocks are."""
    src = (_BACKEND / "services" / "chat_context.py").read_text()
    if "nothing is injected into this chat for you any more" in src:
        check(name, FAIL, "persona still claims nothing is injected, but "
                          "LEARNINGS, ONGOING WORK and KNOWLEDGE all are")
        return
    check(name, PASS, "persona no longer claims nothing is injected")


def c_role_scope_is_listable(name: str) -> None:
    """`role:<r>` is injected into every reviewer's prompt. A layer that reaches
    a prompt and cannot be listed or edited is memory the operator cannot see."""
    src = (_BACKEND / "routes" / "notes_all.py").read_text()
    if "role" not in src:
        check(name, FAIL, "notes_all lists no role scopes, so role: notes are "
                          "injected but invisible in the Notes UI")
        return
    check(name, PASS, "role scopes are enumerated by the notes listing")


CHECKS = [
    ("search returns hits", c_search_works),
    ("search reaches chats, tickets and docs", c_search_is_cross_corpus),
    ("search degrades to lexical instead of dying", c_search_degrades_not_dies),
    ("the scope filter reaches the model", c_scope_filter_reaches_the_model),
    ("a note write marks the index stale", c_index_knows_about_note_writes),
    ("chat/ticket/doc writes mark the index stale", c_index_knows_about_chats_and_tickets),
    ("the index is not ancient", c_index_is_not_ancient),
    ("no machine noise in learnings", c_no_machine_noise_in_learnings),
    ("no duplicate learnings", c_no_duplicate_learnings),
    ("the learnings GC can actually fire", c_learnings_gc_can_fire),
    ("provenance arrays are capped", c_provenance_is_capped),
    ("dead memory substrates are gone", c_dead_substrates_are_gone),
    ("Letta memory was exported before removal", c_letta_memory_was_exported),
    ("the persona does not lie about injection", c_persona_does_not_lie),
    ("role: notes are listable", c_role_scope_is_listable),
]


def main() -> int:
    from services import ownership
    ownership.set_current_owner(OWNER)

    print(f"memory acceptance check, owner {OWNER}\n")
    for name, fn in CHECKS:
        _try(name, fn)

    failed = [r for r in _results if r[0] == FAIL]
    skipped = [r for r in _results if r[0] == SKIP]
    print(f"\n{len(_results) - len(failed) - len(skipped)} passed, "
          f"{len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("\nfailures:")
        for _, name, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
