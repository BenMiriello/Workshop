#!/usr/bin/env python3
"""How many live records actually carry each field.

This is the tool that made the 2026-08-16 coherence audits work, promoted to a
script so the next pass does not have to rediscover it. See R2 in
docs/platform-redesign/coherence-overhaul-2026-08-16.md.

The premise: a field at 0% occupancy is a design that never landed, and the
system is full of them. `ticket.parent` is 0 of 197 and has an ADR, a ratified
depth cap, a decomposition design and a Postgres depth-guard trigger behind it.
`ticket.latitude` is 0 of 197 and has its own module. Both read as live concepts
in every doc and in the schema; only counting reveals otherwise.

The inverse is just as important and is how the ticket-store data loss was
found: a field with real occupancy that a newer write path silently drops. So
this reports occupancy per corpus AND, for tickets, per store, because the two
stores diverging is the interesting case.

Usage:
    cd backend && .venv/bin/python ../scripts/field_occupancy.py
    cd backend && .venv/bin/python ../scripts/field_occupancy.py --json
    cd backend && .venv/bin/python ../scripts/field_occupancy.py --zero-only

Exit status is 0 always. This measures; it does not judge. Wiring a threshold
into a gate is a separate decision, deliberately not made here: a field at zero
is a question ("was this ever built?"), not automatically a defect.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# The data owner every count is taken as. The app partitions personal stores by
# owner id, so an unpinned call raises rather than silently reading nothing.
# 6 is the operator (benmiriello@gmail.com), NOT the Linux uid.
OWNER = 6


def _empty(v: object) -> bool:
    """Absent, null, or an empty container. `0` and `False` are NOT empty:
    an explicit zero is a value someone wrote."""
    if v is None:
        return True
    if isinstance(v, (str, list, dict, tuple, set)):
        return len(v) == 0
    return False


def _occupancy(records: list[dict]) -> tuple[int, Counter, dict[str, object]]:
    """Per-key count of records where the key is present and non-empty, plus
    the fields that are present everywhere and always hold the SAME value.

    That second signal matters as much as the first. `ticket.attempts` is
    present on 198 of 198 records and is the literal 0 on every relational one,
    because the read path derives it from a table nothing writes. Counted as
    occupancy it looks perfectly healthy at 100%. Counted as distinct values it
    is a constant, which is what it actually is. `assignee` is the same story:
    always the literal "operator", hardcoded in the read shape.
    """
    filled: Counter = Counter()
    seen: dict[str, set] = {}
    unhashable: set[str] = set()
    for r in records:
        for k, v in r.items():
            if not _empty(v):
                filled[k] += 1
            if k in unhashable:
                continue
            try:
                seen.setdefault(k, set()).add(v if isinstance(v, (str, int, float, bool, type(None))) else json.dumps(v, sort_keys=True))
            except (TypeError, ValueError):
                unhashable.add(k)
                seen.pop(k, None)
    total = len(records)
    constant = {k: next(iter(vs)) for k, vs in seen.items()
                if len(vs) == 1 and filled.get(k, 0) == total and total > 1}
    return total, filled, constant


def _all_keys(records: list[dict]) -> set[str]:
    keys: set[str] = set()
    for r in records:
        keys.update(r.keys())
    return keys


def collect_tickets() -> dict[str, list[dict]]:
    """Tickets per scope, through the app's own read path.

    Split by store, because `development` and `workshop` are Postgres-backed
    since 2026-08-15 while the project trees are still .tasks.json, and a field
    that is populated in one and empty in the other is exactly the signal worth
    surfacing.
    """
    from routes.tasks_all import scope_roots
    from services import tasks

    out: dict[str, list[dict]] = {}
    for scope, root in scope_roots():
        try:
            out[scope] = tasks.list_tasks(root, include_done=True)
        except Exception as e:  # a scope that cannot be read is a finding
            print(f"  ! {scope}: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def collect_jobs() -> list[dict]:
    """Jobs straight from the append-only log, not through `jobs.read`.

    `jobs.read` hard-caps at 1000 (`jobs.py:275`), so asking it for everything
    silently returns a page. A measurement that reports a page as the corpus is
    worse than no measurement. Collapse by id, later line wins, matching
    `jobs._collapse`.
    """
    from services import paths

    log = paths.env_path("WORKSHOP_JOBS_LOG", "jobs.jsonl")
    if not Path(log).is_file():
        return []
    merged: dict[str, dict] = {}
    for line in Path(log).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        jid = row.get("id")
        if not jid:
            continue
        merged.setdefault(jid, {}).update(row)
    return list(merged.values())


def collect_threads() -> list[dict]:
    """Chat thread metadata, read as files.

    Deliberately not via chat_store: the point is to see the stored shape,
    including keys the read path would normalise away or derive.
    """
    from services import ownership

    root = ownership.user_runtime_dir() / "chat"
    if not root.is_dir():
        return []
    out = []
    for meta in sorted(root.glob("*/meta.json")):
        try:
            out.append(json.loads(meta.read_text()))
        except Exception:
            continue
    return out


def collect_learnings() -> list[dict]:
    from services import learnings, paths

    root = paths.runtime_dir() / "learnings"
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


CORPORA = {
    "job": collect_jobs,
    "chat_thread": collect_threads,
    "learning": collect_learnings,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--zero-only", action="store_true",
                    help="only report fields no live record carries")
    args = ap.parse_args()

    from services import ownership
    ownership.set_current_owner(OWNER)

    report: dict[str, dict] = {}

    # Tickets, split by scope so a divergence between stores is visible.
    per_scope = collect_tickets()
    combined: list[dict] = [t for ts in per_scope.values() for t in ts]
    if combined:
        total, filled, constant = _occupancy(combined)
        report["ticket"] = {
            "total": total,
            "fields": {k: filled.get(k, 0) for k in sorted(_all_keys(combined))},
            "constant": constant,
            "by_scope": {s: len(ts) for s, ts in per_scope.items()},
        }

    for name, fn in CORPORA.items():
        try:
            records = fn()
        except Exception as e:
            print(f"  ! {name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if not records:
            continue
        total, filled, constant = _occupancy(records)
        report[name] = {
            "total": total,
            "fields": {k: filled.get(k, 0) for k in sorted(_all_keys(records))},
            "constant": constant,
        }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    for corpus, data in report.items():
        total = data["total"]
        print(f"\n=== {corpus}  ({total} live records)")
        if "by_scope" in data:
            spread = "  ".join(f"{s}:{n}" for s, n in sorted(data["by_scope"].items()))
            print(f"    by scope: {spread}")
        rows = sorted(data["fields"].items(), key=lambda kv: (kv[1], kv[0]))
        for field, n in rows:
            if args.zero_only and n:
                continue
            pct = (n / total * 100) if total else 0.0
            flag = "  <- never populated" if n == 0 else ""
            if not flag and field in data.get("constant", {}):
                flag = f"  <- always {data['constant'][field]!r}"
            print(f"    {field:22} {n:5d} / {total:<5d} {pct:5.1f}%{flag}")

    zeros = {c: [f for f, n in d["fields"].items() if n == 0] for c, d in report.items()}
    zeros = {c: fs for c, fs in zeros.items() if fs}
    if zeros:
        print("\n=== fields no live record carries")
        print("    Each is either a design that never landed or a write path that "
              "dropped it.\n    Both are worth an answer before building on them.")
        for corpus, fields in sorted(zeros.items()):
            print(f"    {corpus}: {', '.join(sorted(fields))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
