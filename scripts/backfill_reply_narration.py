#!/usr/bin/env python3
"""Rewrite already-stored reply bodies so each one is the ANSWER alone.

WHAT WAS WRONG. Until a0c5fb54, `build_streaming._turn_content` derived a reply's
stored `content` by joining EVERY top-level text segment. A turn emits two kinds
of text and only one of them is the answer: narration has work after it, the
answer does not. So the stored body was the answer with the whole turn's
play-by-play concatenated onto the front of it.

The renderer was never fooled (it splits at the trailing run and always has), so
this was invisible in the transcript and wrong everywhere else `content` is read:
the copy button, the chat-list preview, search, notifications, and the context
handed to the next turn. New replies have been correct since a0c5fb54. This fixes
the ones already on disk.

THE RULE IS IMPORTED, NOT REIMPLEMENTED. `content_from_segments` is the same
function the live path uses. A migration carrying its own copy of "what did the
turn say" would be a second definition of exactly the thing whose duplication
caused this.

SAFETY. Segments are never touched, only the derived `content`. Every file is
copied to a timestamped backup directory before it is written. And a reply is
only rewritten when the new body is a SUFFIX of the old one (modulo whitespace):
that is what "we removed a narration prefix and nothing else" means, and anything
that fails the test is reported and left alone rather than guessed at.

Both the live `log.jsonl` and the rotated `log.N.jsonl` archives are covered. A
seq can legitimately appear in both after an interrupted rotation, so both copies
get the same treatment and stay consistent.

    python3 scripts/backfill_reply_narration.py --dry-run
    python3 scripts/backfill_reply_narration.py

Re-running after a successful pass is a no-op.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from services.build_streaming import content_from_segments  # noqa: E402

# `.runtime/users/<owner>/chat/<thread>/log.jsonl` plus its rotated archives.
LOG_GLOB = ".runtime/users/*/chat/*/log*.jsonl"
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Whitespace-insensitive form, for the suffix test only.

    The old body joined segments with no separator and the new one is a slice of
    the same text, so they agree up to the whitespace at the seam. Comparing
    normalised keeps a stray newline from failing an otherwise clean rewrite."""
    return _WS.sub(" ", s).strip()


def plan_line(obj: dict) -> tuple[str, str] | None:
    """The (old, new) body for this event, or None if it is not in scope.

    In scope: an assistant message carrying segments. Everything else in the log
    (the operator's own messages, turn records, boundaries, titles) is left
    exactly as it is."""
    if obj.get("kind") != "message" or obj.get("role") != "assistant":
        return None
    meta = obj.get("meta")
    if not isinstance(meta, dict):
        return None
    segs = meta.get("segments")
    if not isinstance(segs, list) or not segs:
        return None
    old = obj.get("content")
    if not isinstance(old, str):
        return None
    new = content_from_segments(segs)

    # A STOP NOTICE IS APPENDED AFTER THE BODY AND IS NOT NARRATION.
    #
    # A turn killed by the step limit or the memory ceiling has its reason
    # appended to the stored body ("This turn stopped after its 40-step limit
    # ..."), so that body is the old join PLUS a tail no segment contains.
    # Rewriting to the answer alone would delete the one sentence explaining why
    # the reply stops mid-thought, which is the opposite of the point. Five
    # replies in this installation are that shape.
    #
    # So when the old body opens with the full join, keep whatever follows it.
    # ALREADY DONE, WITH ITS NOTICE STILL ON. A body this migration has already
    # rewritten reads "<answer><stop notice>", which opens with the answer and so
    # has no narration prefix left to remove. Without this it would fail the
    # suffix test on every future run and be reported as an anomaly forever, which
    # trains the reader to ignore the anomaly list.
    if new and _norm(old).startswith(_norm(new)):
        return (old, old)

    join = "".join(s.get("text", "") for s in segs if s.get("t") == "text").strip()
    if join and new:
        at = old.find(join)
        if at >= 0:
            tail = old[at + len(join):]
            if tail.strip():
                return (old, new + tail)
    return (old, new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    ap.add_argument("--root", default=str(ROOT), help="tree to operate on")
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted(root.glob(LOG_GLOB))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup_root = root / ".runtime" / "backups" / f"narration-backfill-{stamp}"

    scanned = rewritten = unchanged = skipped = busy = 0
    removed = 0
    files_touched = 0
    oddities: list[str] = []

    for path in files:
        try:
            # THE LOG IS LIVE AND SO IS THE INSTALLATION. A turn running right
            # now is appending to its own thread's log, and this rewrites whole
            # files. Read the mtime BEFORE the content, re-check it immediately
            # before the rename, and skip the file if it moved: an append that
            # landed in that window would otherwise be silently dropped. This is
            # the one way a migration over a running system eats a message.
            mtime_before = path.stat().st_mtime_ns
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            oddities.append(f"unreadable {path}: {e}")
            continue

        out: list[str] = []
        changed_here = 0
        removed_here = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                out.append(line)
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                # A torn tail line from an interrupted append. Rotation heals
                # those; this migration must not eat one.
                out.append(line)
                continue
            if not isinstance(obj, dict):
                out.append(line)
                continue

            plan = plan_line(obj)
            if plan is None:
                out.append(line)
                continue
            scanned += 1
            old, new = plan
            if _norm(old) == _norm(new):
                unchanged += 1
                out.append(line)
                continue
            # THE SUFFIX TEST. The new body must be the tail of the old one: all
            # this rewrite may do is drop a narration prefix. Anything else means
            # the stored body did not come from these segments (a hand-edited
            # transcript, an older writer, a scrub that changed the text), and
            # the right move is to report it and leave it alone.
            if not _norm(old).endswith(_norm(new)) or not new:
                skipped += 1
                oddities.append(
                    f"{path.parent.name[:8]} seq={obj.get('seq')}: new body is not a "
                    f"suffix of the old (old {len(old)}c, new {len(new)}c); left alone"
                )
                out.append(line)
                continue
            obj["content"] = new
            removed_here += len(old) - len(new)
            changed_here += 1
            out.append(json.dumps(obj, ensure_ascii=False))

        if not changed_here:
            continue
        if args.dry_run:
            rewritten += changed_here
            removed += removed_here
            files_touched += 1
            continue

        # Somebody appended while we were working. Leave it entirely alone; a
        # re-run picks it up once the turn has settled.
        if path.stat().st_mtime_ns != mtime_before:
            busy += 1
            oddities.append(f"{path.parent.name[:8]}: appended to while running, skipped whole file")
            continue

        rewritten += changed_here
        removed += removed_here
        files_touched += 1
        rel = path.relative_to(root)
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        # Same write discipline the log itself uses: full file to a temp beside
        # it, fsync, then rename over. A crash cannot leave a half-written log.
        tmp = path.with_suffix(path.suffix + ".tmp")
        body = "\n".join(out)
        if raw.endswith("\n"):
            body += "\n"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    mode = "DRY RUN, nothing written" if args.dry_run else "written"
    print(f"backfill_reply_narration ({mode})")
    print(f"  log files            : {len(files)}")
    print(f"  replies scanned      : {scanned}")
    print(f"  replies rewritten    : {rewritten}")
    print(f"  already correct      : {unchanged}")
    print(f"  skipped (not suffix) : {skipped}")
    print(f"  files busy (skipped) : {busy}")
    print(f"  narration removed    : {removed:,} chars")
    print(f"  files touched        : {files_touched}")
    if not args.dry_run and files_touched:
        print(f"  backup               : {backup_root.relative_to(root)}")
    if oddities:
        print(f"\n  {len(oddities)} left alone:")
        for o in oddities[:20]:
            print(f"    {o}")
        if len(oddities) > 20:
            print(f"    ... and {len(oddities) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
