#!/usr/bin/env python3
"""No mutable STATE files committed under a source directory. Layer 0 ratchet of
docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md
(§9 "Layer 0 — restructure"): everything mutable lives under the untracked runtime
root and is addressed only through `wsplatform/paths.py`, never checked into
`backend/ frontend/ packages/`.

WHAT IT CATCHES. A tracked `*.json` / `*.jsonl` / `*.db` / `*.sqlite[3]` under one
of the source directories that is not clearly config, a schema, an example, or a
test fixture. That is the shape of the four files this stage evicted
(`grants.json` and siblings): instance state that drifted into the source tree,
so a fresh clone shipped one operator's data and code and data shared a mtime.

WHY A PINNED BASELINE, NOT AN ABSOLUTE ZERO. `backend/grants.json` is still
tracked in git at the time this ratchet lands — it is relocated to the runtime
root at startup and the human deletes the stale tracked copy post-verify (the
plan moves it now, deletes it after the move is confirmed). Failing on it today
would fail the reviewer for the very change that fixes it, the exact "a checker
that fires constantly gets disabled" failure `check_one_definition.py`'s header
warns about. So the current set is pinned in `no_state_in_source_baseline.json`
and this fails only on a NEW state file. When the human removes the tracked
`grants.json`, re-run `--write-baseline` to drop it and gate at zero.

Config, schema, example and fixture files are excluded up front (npm/tsconfig
manifests, `*.example.json`, `*.schema.json`, anything under a tests/fixtures
tree), because those are source, not state — they do not change at runtime.

Usage:
    python3 scripts/check_no_state_in_source.py                 # exit 1 on a new state file
    python3 scripts/check_no_state_in_source.py --json
    python3 scripts/check_no_state_in_source.py --write-baseline
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_BASELINE_PATH = Path(__file__).resolve().parent / "no_state_in_source_baseline.json"

# Directories a committed state file may not live under (the source tree).
_SOURCE_DIRS = ("backend/", "frontend/", "packages/")

# The extensions that name a mutable/state store.
_STATE_SUFFIXES = (".json", ".jsonl", ".db", ".sqlite", ".sqlite3")

# Basenames that are config or a build manifest, not runtime state.
_CONFIG_BASENAMES = {
    "package.json", "package-lock.json", "jsconfig.json", "components.json",
    "manifest.json", "app.json", "eas.json", "vercel.json",
}

# Basename glob patterns that name config / schema / example / lock files.
_CONFIG_GLOBS = (
    "tsconfig*.json", "*.config.json", "*.schema.json", "*.example.json",
    "*.tpl.json", "*.template.json", "*-lock.json", "*.lock.json",
)

# Path fragments that mark a tree of test fixtures / examples (source, not state).
_FIXTURE_PARTS = (
    "/tests/", "/test/", "/__tests__/", "/__fixtures__/", "/fixtures/",
    "/fixture/", "/testdata/", "/examples/", "/example/", "/__mocks__/",
    "/node_modules/",
)


def _tracked_files() -> list[str]:
    """git-tracked paths, repo-relative and posix. 'Committed' is the whole point
    of the ratchet: an untracked scratch json is not what this guards against."""
    out = subprocess.run(
        ["git", "-C", str(_WORKSHOP), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _is_excluded(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in _CONFIG_BASENAMES:
        return True
    if any(fnmatch.fnmatch(name, g) for g in _CONFIG_GLOBS):
        return True
    if any(part in f"/{rel}" for part in _FIXTURE_PARTS):
        return True
    return False


def _candidates() -> list[str]:
    """Tracked state-like files under a source dir that are not config/fixture."""
    out = []
    for rel in _tracked_files():
        if not rel.startswith(_SOURCE_DIRS):
            continue
        if not rel.endswith(_STATE_SUFFIXES):
            continue
        if _is_excluded(rel):
            continue
        out.append(rel)
    return sorted(out)


def _load_baseline(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    allowed = data.get("allowed") if isinstance(data, dict) else None
    return sorted(allowed) if isinstance(allowed, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true",
                    help="pin the current set of state files as the allowlist and exit")
    args = ap.parse_args()

    baseline_path = Path(args.baseline)
    current = _candidates()

    if args.write_baseline:
        payload = {
            "allowed": current,
            "_note": ("state files tracked under a source dir today; a NEW one not "
                      "in this list fails the ratchet. Drop entries as they are "
                      "relocated. See scripts/check_no_state_in_source.py."),
        }
        baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"check_no_state_in_source: wrote baseline to {baseline_path}: {current}")
        return 0

    allowed = _load_baseline(baseline_path)
    offenders = [p for p in current if p not in allowed]

    if args.json:
        print(json.dumps({
            "current": current, "allowed": allowed, "offenders": offenders,
        }, indent=2))
        return 1 if offenders else 0

    if offenders:
        print(f"check_no_state_in_source: {len(offenders)} new state file(s) "
              "committed under a source directory:")
        for p in offenders:
            print(f"    {p}")
        print("\nMutable state does not belong in the source tree. Put it under the "
              "runtime root and address it via wsplatform/paths.py. If this really "
              "is config/schema/fixture, name it so (*.example.json, a tests/ tree, "
              "etc.) or, deliberately, re-pin with --write-baseline after review.")
        return 1

    print(f"check_no_state_in_source: no new state file under {list(_SOURCE_DIRS)} "
          f"(allowlisted baseline: {allowed or 'empty'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
