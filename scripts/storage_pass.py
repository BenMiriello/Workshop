#!/usr/bin/env python3
"""Run the storage retention pass (backend/services/storage/expire.py).

    storage_pass.py                 report what an applied pass would do
    storage_pass.py --apply         do it
    --if-older-than SECONDS         skip when the last applied pass is newer,
                                    unless a disk is under its keep-free floor

The backend schedule runs `--apply --if-older-than 86400` every few minutes:
a day apart while there is room, and at once whenever a disk runs short.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from services import ownership  # noqa: E402
from services.storage import expire, policy  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--if-older-than", type=float, default=0)
    args = ap.parse_args()
    ownership.pin_operator()
    last = expire.last()
    recent = last and time.time() - last["started_at"] < args.if_older_than
    if args.if_older_than and recent and not expire.short(policy.read()):
        print(json.dumps({"skipped": "last pass is recent", "last": last["started_at"]}))
        return 0
    print(json.dumps(expire.run(apply=args.apply), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
