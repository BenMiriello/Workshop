#!/usr/bin/env python3
"""Retire the `build:<slug>` context key: rewrite every stored spelling to `agent:<slug>`.

`agent:<slug>` became the durable context key for a chat with an agent on
2026-08-16. `build:<slug>` survived after that only as a FOLD -- a second
spelling the read path translated -- which is the layering the coherence
overhaul exists to remove. This rewrites the stored data so the fold can be
deleted from the code.

Two stores hold a context key and both are covered:

  * the per-owner chat log on disk (`<runtime>/users/<uid>/chat/<id>/meta.json`),
    which is the authoritative store; and
  * the `chat_thread` table, which mirrors it for the list/search surfaces.

Tickets are covered too. A ticket carries its scope by virtue of WHICH STORE it
lives in rather than as a field, so the only ticket-side spellings a `build:`
key can reach are the provenance refs (`source_ref`, `external_id`) that record
where a ticket came from.

IDEMPOTENT. Running it twice is a no-op: the second pass finds nothing left
starting with `build:`. Safe to run before or after the code change.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

PREFIX = "build:"


def _canonical(key: str) -> str:
    """The one spelling. An empty slug (`build:`) names no agent, so it falls
    back to the unbound chat rather than minting `agent:`."""
    slug = key[len(PREFIX):]
    return f"agent:{slug}" if slug else "general"


def migrate_chat_files(runtime: Path) -> tuple[int, int]:
    """(threads carrying a `build:` key, threads rewritten) across every owner."""
    found = fixed = 0
    for meta_path in sorted(runtime.glob("users/*/chat/*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        changed = False
        for field in ("context_key", "scope"):
            val = meta.get(field)
            if isinstance(val, str) and val.startswith(PREFIX):
                if field == "context_key":
                    found += 1
                meta[field] = _canonical(val)
                changed = True
        if changed:
            meta_path.write_text(json.dumps(meta, indent=2))
            fixed += 1
    return found, fixed


def migrate_db() -> dict:
    """Rewrite the mirrored thread rows and any ticket provenance ref.

    Returns counts per table. An unreachable database is reported, not raised:
    the disk store above is the authoritative one and a deployment without the
    mirror is a valid deployment."""
    import sqlalchemy as sa

    url = (os.environ.get("WORKSHOP_DATABASE_ADMIN_URL")
           or os.environ.get("WORKSHOP_DATABASE_URL")
           or os.environ.get("DATABASE_URL"))
    if not url:
        return {"error": "no database URL in the environment"}
    engine = sa.create_engine(url)
    out: dict = {}
    with engine.begin() as conn:
        for table, cols in (("chat_thread", ("context_key", "scope")),
                            ("ticket", ("source_ref", "external_id"))):
            for col in cols:
                before = conn.execute(sa.text(
                    f"select count(*) from {table} where {col} like :p"), {"p": f"{PREFIX}%"}).scalar()
                if before:
                    conn.execute(sa.text(
                        f"update {table} set {col} = 'agent:' || substring({col} from :n) "
                        f"where {col} like :p and length({col}) > :n1"),
                        {"n": len(PREFIX) + 1, "n1": len(PREFIX), "p": f"{PREFIX}%"})
                    conn.execute(sa.text(
                        f"update {table} set {col} = 'general' where {col} = :exact"),
                        {"exact": PREFIX})
                after = conn.execute(sa.text(
                    f"select count(*) from {table} where {col} like :p"), {"p": f"{PREFIX}%"}).scalar()
                out[f"{table}.{col}"] = {"before": before, "after": after}
    return out


def main() -> int:
    from services import paths

    runtime = paths.runtime_dir()
    found, fixed = migrate_chat_files(runtime)
    print(f"chat files under {runtime}: {found} thread(s) carried a build: context key, "
          f"{fixed} file(s) rewritten")
    try:
        for name, counts in migrate_db().items():
            print(f"db {name}: {counts}")
    except Exception as e:  # noqa: BLE001 - the disk store is authoritative
        print(f"db: skipped ({e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
