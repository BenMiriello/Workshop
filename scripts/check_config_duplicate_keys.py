#!/usr/bin/env python3
"""check_config_duplicate_keys.py -- one config key, one value, on this install.

WHY THIS EXISTS. Measured 2026-09-17: `WORKSHOP_OPERATOR_NAME` was declared in
both `~/.config/workshop/instance.env` and `~/.config/workshop/secrets.env`
with two different values -- not quoted here, because naming this install's
operator in tracked source is the very thing `check_no_hardcoding.sh` forbids,
and this docstring was failing that gate -- and three different
loaders disagreed about which one won -- the running backend, an interactive
script, and cron each answered differently, silently. `WORKSHOP_DEV_MODE` and
one app-specific channel id were ALSO declared twice and happened to agree,
which is not safety, it is the same defect not yet triggered: the first time
either copy is edited alone, it diverges too.

This does not stop a key from being declared in more than one file -- that is
sometimes fine (a value repeated for clarity, or read by two independent
loaders that are expected to agree). It fails only when two files declare the
SAME key with DIFFERENT values, because that is the situation where "what is
WORKSHOP_OPERATOR_NAME" has no single answer and depends on who is asking.

WHAT IT READS. The same files, from the same directory, that
`backend/db/script_env.py` reads (`script_env.CONFIG_FILES`, imported rather
than restated here -- one file list, not two). Those files live outside this
repo, in the operator's own config directory, and are entirely optional: a
fresh clone with none of them present is clean by definition, not a failure.

WHAT IT NEVER DOES. Print, log or return a value read from any config file.
Only key names and which files declared them are reported -- a conflicting
value is real information about a misconfiguration, but the values themselves
may be secrets, and nothing about diagnosing "these two disagree" requires
showing what either of them is.

Usage:
    python3 scripts/check_config_duplicate_keys.py            # human-readable
    python3 scripts/check_config_duplicate_keys.py --json      # machine-readable

Exit 0 = clean (including "no config files present"), 1 = a conflict exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_WORKSHOP / "backend"))

from db import script_env  # noqa: E402


def _declarations() -> dict[str, dict[str, str]]:
    """key -> {filename: value}, over every CONFIG_FILES entry that exists."""
    by_key: dict[str, dict[str, str]] = {}
    for name in script_env.CONFIG_FILES:
        path = script_env.config_dir() / name
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for key, value in script_env.parse_env_file(text).items():
            by_key.setdefault(key, {})[name] = value
    return by_key


def find_conflicts() -> list[dict[str, object]]:
    conflicts = []
    for key, by_file in sorted(_declarations().items()):
        if len(by_file) < 2:
            continue
        if len(set(by_file.values())) > 1:
            conflicts.append({"key": key, "files": sorted(by_file)})
    return conflicts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conflicts = find_conflicts()

    if args.json:
        print(json.dumps({
            "status": "conflict" if conflicts else "ok",
            "conflicts": conflicts,
        }, indent=2))
        return 1 if conflicts else 0

    if not conflicts:
        print(f"check_config_duplicate_keys: clean over {script_env.CONFIG_FILES} "
              f"in {script_env.config_dir()}")
        return 0

    print(f"check_config_duplicate_keys: {len(conflicts)} key(s) declared with "
          "conflicting values across more than one config file")
    for c in conflicts:
        print(f"    {c['key']}  declared differently in: {', '.join(c['files'])}")
    print("\nA key with more than one answer depends on which loader read it "
          "first. Keep exactly one declaration and delete the others (never "
          "print or paste the actual values into a ticket, chat or commit).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
