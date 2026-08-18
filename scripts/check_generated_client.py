#!/usr/bin/env python3
"""The generated API types are STALE, ratcheted. The enforcement half of
`docs/platform-redesign/target-architecture-2026-08-17.md` item 2 -- read that
section for the narrative; `scripts/gen_openapi.py` is the generator this
checks, and this script is what makes regenerating non-optional.

THE FINDING THIS PROTECTS. Measured 2026-08-17: the frontend hand-writes 528
response types and casts at 232 `.json()` sites, 54 of them with no annotation
at all, and three of the four endpoints spot-checked had already drifted from
what their route actually returns. Nothing in the repo could detect that,
because there was no machine-readable statement of what a route returns to
compare against. `response_model=` (see `check_response_model.py`) makes the
backend say it; `gen_openapi.py` turns that into TypeScript; and this check is
the part that makes the TypeScript keep up. Without it the generated file is
just a snapshot that silently rots, which is the ordinary fate of committed
codegen.

WHAT IT DOES. Regenerates the schema and the types from the CURRENT source
tree into a temporary directory, and compares the result byte-for-byte with
the committed `frontend/src/api/schema.gen.ts`. It does not look at
timestamps, at git history, or at whether the route files appear in the diff:
a timestamp check passes for anyone who touches the generated file for any
reason, and a diff-scoped check misses a contract that moved because a
`services/` model changed under a route nobody edited. Regenerating is the
only comparison that cannot be fooled, and it costs about four seconds.

IT FIRES ON DOCSTRINGS TOO, AND THAT IS THE INTENDED TRADE. FastAPI puts a
route's docstring into the schema as its `description`, and openapi-typescript
puts that into the generated file as JSDoc, so rewording a route's docstring
makes this check go red -- and this repo rewords docstrings constantly. The
alternative, stripping descriptions before generating, buys a quieter check by
throwing away the editor tooltips that are half of why generated types are
worth having, and it would also mean the committed file is NOT what the
generator produces, which is a second thing to keep in sync. The remedy here
is one command that takes under two seconds, so the friction is real but
small, and the check keeps saying something true: this artifact does not match
this source tree.

WHY THIS ONE IS AN ABSOLUTE GATE, NOT A PINNED-NUMBER RATCHET.
`check_response_model.py` and `check_module_layout.py` pin a baseline because
the thing they measure is debt that already exists in the thousands and cannot
be paid off in one diff. This measures something with exactly one correct
value: either the committed file is what the generator produces from this tree
or it is not. There is no accumulated debt to grandfather and no unrelated
task that legitimately leaves it wrong, so there is nothing to ratchet -- the
remedy is always the same single command, `npm run gen:api-types`, and the
check prints it.

Usage:
    python3 scripts/check_generated_client.py            # exit 1 when stale
    python3 scripts/check_generated_client.py --json
    python3 scripts/check_generated_client.py --diff     # show the drift
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_GENERATOR = _WORKSHOP / "scripts" / "gen_openapi.py"
_COMMITTED_TS = _WORKSHOP / "frontend" / "src" / "api" / "schema.gen.ts"

_REGEN_COMMAND = "npm run gen:api-types"

_MAX_DIFF_LINES = 60


def _regenerate(into: Path) -> tuple[str | None, str]:
    """Run the generator into `into`. Returns (types_text, error_message)."""
    ts_out = into / "schema.gen.ts"
    schema_out = into / "openapi.json"
    try:
        res = subprocess.run(
            [sys.executable, str(_GENERATOR),
             "--schema-out", str(schema_out),
             "--ts-out", str(ts_out),
             "--quiet"],
            cwd=_WORKSHOP, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not run {_GENERATOR.name}: {exc}"
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip().splitlines()[-12:]
        return None, (f"{_GENERATOR.name} exited {res.returncode}:\n    "
                      + "\n    ".join(tail))
    try:
        return ts_out.read_text(encoding="utf-8"), ""
    except OSError as exc:
        return None, f"{_GENERATOR.name} wrote no types: {exc}"


def check() -> dict:
    committed_exists = _COMMITTED_TS.is_file()
    committed = _COMMITTED_TS.read_text(encoding="utf-8") if committed_exists else ""

    tmp = Path(tempfile.mkdtemp(prefix="workshop-genclient-"))
    try:
        fresh, error = _regenerate(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    rel = _COMMITTED_TS.relative_to(_WORKSHOP).as_posix()

    if fresh is None:
        return {"ok": False, "reason": "generator_failed", "file": rel,
                "error": error, "diff": []}

    if not committed_exists:
        return {"ok": False, "reason": "missing", "file": rel,
                "error": f"{rel} does not exist", "diff": []}

    if committed == fresh:
        return {"ok": True, "reason": "up_to_date", "file": rel,
                "error": "", "diff": [],
                "lines": committed.count("\n")}

    diff = list(difflib.unified_diff(
        committed.splitlines(), fresh.splitlines(),
        fromfile=f"{rel} (committed)", tofile=f"{rel} (regenerated from source)",
        lineterm="", n=2,
    ))
    changed = sum(1 for l in diff if l.startswith(("+", "-"))
                  and not l.startswith(("+++", "---")))
    return {"ok": False, "reason": "stale", "file": rel,
            "error": f"{changed} line(s) differ", "diff": diff,
            "changed_lines": changed}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--diff", action="store_true",
                    help="print the full drift instead of the first "
                         f"{_MAX_DIFF_LINES} lines")
    args = ap.parse_args()

    result = check()

    if args.json:
        payload = dict(result)
        if not args.diff:
            payload["diff"] = result["diff"][:_MAX_DIFF_LINES]
        print(json.dumps(payload, indent=2))
        return 0 if result["ok"] else 1

    if result["ok"]:
        print(f"check_generated_client: {result['file']} is up to date with the "
              f"backend schema ({result.get('lines', 0)} lines)")
        return 0

    if result["reason"] == "generator_failed":
        print(f"check_generated_client: could not regenerate -- {result['error']}")
        return 1

    if result["reason"] == "missing":
        print(f"check_generated_client: {result['file']} is missing. "
              f"Run `{_REGEN_COMMAND}`.")
        return 1

    print(f"=== check_generated_client: {result['file']} is STALE "
          f"({result['error']})")
    print("    A route's contract changed and the generated types were not "
          "regenerated, so")
    print("    the frontend is now typechecking against a shape the backend no "
          "longer serves.")
    shown = result["diff"] if args.diff else result["diff"][:_MAX_DIFF_LINES]
    for line in shown:
        print(f"    {line}")
    if len(shown) < len(result["diff"]):
        print(f"    ... {len(result['diff']) - len(shown)} more diff line(s); "
              f"re-run with --diff")
    print(f"\ncheck_generated_client: run `{_REGEN_COMMAND}` and commit the result.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
