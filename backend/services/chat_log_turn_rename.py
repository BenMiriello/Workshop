"""One-shot: rename the chat store's on-disk `run` vocabulary to `turn`.

WHY THIS EXISTS AT ALL. One value -- the claim a streaming turn holds on a
thread -- was called `run_id` in the JSONL log and in `chat_turn.run_id`,
and `turn_id` on the wire, with `db/chat_repo._row` translating between them.
The code, the wire and the Postgres column moved to `turn_id` in one commit
(2026-08-17). The file store is the fourth place the value is written, and
the only one with data already on disk, so it gets a migration instead of a
compatibility read: a reader that accepts both names is the same translation
layer under a different roof, and it never goes away.

WHAT IT RENAMES, and nothing else:

  log.jsonl / log.1.jsonl
      event `kind`      "run"          -> "turn"
      event             `run_id`       -> `turn_id`
      message `meta`    `during_run`   -> `during_turn`
  meta.json
      `active_run_id`   -> `active_turn_id`
      `active_run_seq`  -> `active_turn_seq`
      `live_message.run_id` -> `live_message.turn_id`
  live.json
      `run_id`          -> `turn_id`

`during_turn` is not a new name: it is the name this field had BEFORE it was
renamed to `during_run`, and both spellings were already on disk (100 and 36
respectively on this installation) with every reader accepting either. So
this direction re-unifies them rather than adding a third spelling, and the
dual read in `chat_context.arrival_notice` and `db/chat_backfill` could be
deleted with it.

SAFETY. A line that needs no change is copied BYTE FOR BYTE, including a torn
or unparseable one -- `chat_log._read_rows` already tolerates those and
dropping one here would silently shorten a transcript. A file with no
matching key is not rewritten at all. The rewrite is atomic per file (temp
file in the same directory, then `os.replace`), and it refuses a file whose
size changed while it was being read, which is the one way a live append
could be lost.

Idempotent: running it twice is a no-op, because the second pass finds no old
keys. `--dry-run` reports what it would change and writes nothing.

WHY IT RUNS AT STARTUP AND NOT BY HAND. The old vocabulary and the old code
belong together, so the moment to rewrite the files is the moment the process
that reads them is the new one. Renaming the keys under a backend that is
still running the previous build would make a turn in flight look unclaimed to
its own writer: `chat_store.update_turn` and `commit_turn` both fence on
reading back the claim they wrote, and neither can find a key that changed
name underneath it. `main.py` calls `run_once()` before the startup reconcile,
which is before anything reads the store. `run_once` is guarded by a marker
file, so every boot after the first costs one `stat`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from services import ownership

_LOG_NAMES = ("log.jsonl", "log.1.jsonl")


def _rewrite_event(ev: dict) -> bool:
    """Rename in place. True if anything changed."""
    changed = False
    if ev.get("kind") == "run":
        ev["kind"] = "turn"
        changed = True
    if "run_id" in ev:
        ev["turn_id"] = ev.pop("run_id")
        changed = True
    meta = ev.get("meta")
    if isinstance(meta, dict) and "during_run" in meta:
        during = meta.pop("during_run")
        # Both spellings on one message would mean two different turns claimed
        # it, which nothing can produce. Keep the older key's value if it is
        # somehow there, rather than inventing a merge rule.
        meta.setdefault("during_turn", during)
        changed = True
    return changed


def _rewrite_meta(doc: dict) -> bool:
    changed = False
    for old, new in (("active_run_id", "active_turn_id"),
                     ("active_run_seq", "active_turn_seq")):
        if old in doc:
            doc[new] = doc.pop(old)
            changed = True
    live = doc.get("live_message")
    if isinstance(live, dict) and "run_id" in live:
        live["turn_id"] = live.pop("run_id")
        changed = True
    return changed


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".rename.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _do_log(path: Path, dry: bool) -> int:
    raw = path.read_bytes()
    out: list[str] = []
    hits = 0
    for line in raw.decode("utf-8", "replace").splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            ev = json.loads(stripped)
        except json.JSONDecodeError:
            out.append(line)              # a torn line stays exactly as it is
            continue
        if not isinstance(ev, dict) or not _rewrite_event(ev):
            out.append(line)
            continue
        hits += 1
        out.append(json.dumps(ev, separators=(",", ":")) + "\n")
    if hits and not dry:
        if path.stat().st_size != len(raw):
            raise RuntimeError(f"{path} was appended to while being read; rerun")
        _atomic_write(path, "".join(out))
    return hits


def _do_json(path: Path, dry: bool) -> int:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(doc, dict):
        return 0
    if path.name == "meta.json":
        changed = _rewrite_meta(doc)
    elif "run_id" in doc:                 # live.json
        doc["turn_id"] = doc.pop("run_id")
        changed = True
    else:
        changed = False
    if changed and not dry:
        _atomic_write(path, json.dumps(doc, indent=2) + "\n")
    return int(changed)


def run(root: Path | None = None, dry: bool = False) -> dict:
    root = root or (ownership._RUNTIME / "users")
    stats = {"threads": 0, "log_events": 0, "meta_files": 0, "live_files": 0}
    for chat_dir in sorted(root.glob("*/chat")):
        for thread in sorted(p for p in chat_dir.iterdir() if p.is_dir()):
            stats["threads"] += 1
            for name in _LOG_NAMES:
                p = thread / name
                if p.exists():
                    stats["log_events"] += _do_log(p, dry)
            for name, key in (("meta.json", "meta_files"), ("live.json", "live_files")):
                p = thread / name
                if p.exists():
                    stats[key] += _do_json(p, dry)
    return stats


MARKER = "chat-turn-rename.done"


def run_once() -> dict | None:
    """The startup entry point. Returns the stats, or None if already done.

    The marker is written even when nothing changed, because "no old keys
    found" and "already migrated" are the same state and re-proving it on
    every boot is the only cost this could have.
    """
    marker = ownership._RUNTIME / MARKER
    if marker.exists():
        return None
    stats = run()
    marker.write_text(json.dumps(stats) + "\n", encoding="utf-8")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", type=Path, default=None,
                    help="the users/ directory; defaults to the live runtime")
    args = ap.parse_args()
    print(run(root=args.root, dry=args.dry_run))
