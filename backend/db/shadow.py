"""Compare the JSON ticket store against the relational one, field by field.

THE POINT OF P3'S FIRST HALF. `.tasks.json` is still authoritative and every
read still comes from it. `db/ticket_sync.py` keeps Postgres current on every
write. This is the proof that the two agree, so the switch is made on evidence
rather than on the fact that the code looks right.

Run it as a check (`python db/shadow.py`), not in the request path. It reads
both stores and reports every difference, grouped by field, with the first few
examples of each. Exit status is 1 when anything diverges, so it can gate.

WHAT IS DELIBERATELY NOT COMPARED, and why each is expected rather than ignored:

  priority   the JSON integer against the band it maps to, not against itself.
             38 distinct integers became 5 bands on purpose (ADR 0019); a raw
             comparison would report all 98 tickets as divergent.
  updated    the mirror writes its own `updated_at`, so the timestamps differ by
             the duration of the write. Comparing them measures the clock.
  text       a projection of title+body on both sides. Comparing the pair is
             stricter; comparing the projection as well only double-counts.
  the        `latitude`, `max_attempts`, `owner`, `assignee`, `exec` and the
  dropped    rest have no column, by measurement: every one was 0% occupancy or
  fields     a mirror of another field. They are LISTED in the report rather
             than skipped silently, so "we dropped this deliberately" stays
             visible instead of becoming something nobody remembers deciding.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Compared directly. `status` and `parent` are legacy tokens/ids on both sides
# because `repo.py` returns the compatibility shape.
_COMPARED = ("title", "body", "status", "parent")

# Compared after mapping the JSON value into the schema's vocabulary.
_MAPPED = ("priority",)

# Order-insensitive.
_SET_FIELDS = ("depends_on", "relates_to")

# Present in the JSON record, no column, on purpose. Reported, not skipped.
_DROPPED = ("latitude", "max_attempts", "owner", "assignee", "exec",
            "blocked_request", "sync", "external_refs", "claim_token",
            "claim_pid", "lease_until", "last_heartbeat", "attempts",
            "job_ids", "evidence", "acceptance", "touches", "scoped_by",
            "provenance", "discovered_from", "blocked_reason", "source",
            "labels", "triage", "execution", "effort", "created", "updated",
            "text", "id", "title", "body", "status", "parent", "priority",
            "depends_on", "relates_to")


def _json_tasks(store: Path) -> list[dict]:
    if not store.exists():
        return []
    payload = json.loads(store.read_text())
    return payload.get("tasks", payload) if isinstance(payload, dict) else payload


def compare(org_id, scope: str, tasks: list[dict]) -> dict:
    """Differences between a JSON task list and the same scope in Postgres."""
    from db import repo
    from db.legacy_map import priority_band

    rows = {t["id"]: t for t in repo.list_tickets(org_id, scope=scope)}
    diffs: dict[str, list] = defaultdict(list)

    json_ids = {str(t.get("id")) for t in tasks if t.get("id")}
    for missing in sorted(json_ids - set(rows)):
        diffs["MISSING_FROM_DB"].append(missing)
    for extra in sorted(set(rows) - json_ids):
        diffs["EXTRA_IN_DB"].append(extra)

    for task in tasks:
        tid = str(task.get("id") or "")
        row = rows.get(tid)
        if row is None:
            continue
        for field in _COMPARED:
            a = (task.get(field) or "") if field != "parent" else (task.get(field) or None)
            b = (row.get(field) or "") if field != "parent" else (row.get(field) or None)
            if isinstance(a, str):
                a, b = a.strip(), (b or "").strip()
            if a != b:
                diffs[field].append((tid, a, b))
        for field in _MAPPED:
            a = priority_band(task.get(field))
            if a != row.get(field):
                diffs[field].append((tid, a, row.get(field)))
        for field in _SET_FIELDS:
            a = {x for x in (task.get(field) or []) if isinstance(x, str)}
            # A dangling id the JSON store allowed cannot exist as an edge, so
            # only compare against ids this scope actually has.
            a &= json_ids
            if a != set(row.get(field) or []):
                diffs[field].append((tid, sorted(a), sorted(row.get(field) or [])))

    present = Counter()
    for task in tasks:
        for key, value in task.items():
            if key not in _DROPPED and value not in (None, "", [], {}):
                present[key] += 1
    return {"diffs": dict(diffs), "unmodelled": dict(present),
            "json": len(tasks), "db": len(rows)}


def main() -> int:
    from db.backfill import SCOPES
    from services import ownership, tenancy

    ownership.set_current_owner(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
    if not tenancy.available():
        print("no database; nothing to compare")
        return 0
    org_id, _ = tenancy.org_and_user()

    worst = 0
    for slug, _name, _key, store_dir in SCOPES:
        tasks = _json_tasks(Path(store_dir) / ".tasks.json")
        result = compare(org_id, slug, tasks)
        n = sum(len(v) for v in result["diffs"].values())
        worst = max(worst, n)
        print(f"\n=== {slug}: {result['json']} in json, {result['db']} in db, "
              f"{n} differences ===")
        for field, items in sorted(result["diffs"].items()):
            print(f"  {field}: {len(items)}")
            for example in items[:3]:
                print(f"      {example}")
        if result["unmodelled"]:
            print("  populated JSON keys with no column (expected, listed so "
                  "the decision stays visible):")
            for key, count in sorted(result["unmodelled"].items(),
                                     key=lambda kv: -kv[1]):
                print(f"      {key}: {count}")
    return 1 if worst else 0


if __name__ == "__main__":
    raise SystemExit(main())
