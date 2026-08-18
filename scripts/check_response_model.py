#!/usr/bin/env python3
"""`response_model=` on every route, ratcheted. Enforces ADR 0023 (the HTTP
contract ADR), derived from `docs/platform-redesign/target-architecture-
2026-08-17.md` §2.4 -- read that section for the narrative; this script is
the mechanical half of it.

THE FINDING THIS PROTECTS. Measured 2026-08-17: request bodies are well
typed (113 pydantic models) but responses are essentially undescribed --
only a handful of routes, all in one small module, declare `response_model=`.
This is why the OpenAPI schema FastAPI generates from this app is not
trustworthy, why the frontend hand-writes hundreds of response types instead
of generating them, and why drift between what a route actually returns and
what the frontend thinks it returns is a class of bug that currently has no
mechanical detector: the worst measured case was a route returning an entire
4.77 MB chat transcript that its own declared type said it did not.
`response_model=` is what makes the schema real; everything downstream in
§2.4 (a generated TypeScript client, drift becoming a compile error) is
blocked on this one attribute actually being present.

WHAT THIS COUNTS. Every `@<name>.<method>(...)` decorator in `backend/routes/`
for an HTTP verb (`get/post/put/patch/delete/head/options` -- NOT
`websocket`, which has no response body and no `response_model` parameter in
FastAPI at all) on a function definition, whether the decorator call passes a
`response_model=` keyword argument, and whether the function itself carries a
return type annotation (`-> SomeModel`). The three numbers are independent:
a route can have a return annotation FastAPI never sees (informational only,
some routes return `dict` or `Any` and call it done), so only the decorator
argument counts toward the ratchet.

WHY A RATCHET, NOT AN ABSOLUTE GATE. 415 routes exist today and 4 declare
`response_model=`. Gating on "every route must declare one" would fail the
reviewer for every unrelated task in the repo starting today -- the same
"fires constantly, gets disabled" failure `check_one_definition.py`'s header
warns against, and the ADR itself is still being written as of this script's
first run. So this pins today's per-file WITHOUT-response_model counts in
`response_model_baseline.json` and fails when a file's own count goes UP --
never when it goes down, and never for a file nobody touched. `--only-changed
<rev>` narrows FAILURE to route files actually in the diff, exactly like
`check_one_definition.py --only-changed`: touching a route file and leaving
it without `response_model=` is fine (that debt predates this diff); adding a
NEW route to that file without one, or removing an existing `response_model=`
from an existing route, is not.

Usage:
    python3 scripts/check_response_model.py                # measure, compare, exit 1 on regression
    python3 scripts/check_response_model.py --json
    python3 scripts/check_response_model.py --only-changed HEAD
    python3 scripts/check_response_model.py --write-baseline
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_ROUTES_DIR = _WORKSHOP / "backend" / "routes"
_BASELINE_PATH = Path(__file__).resolve().parent / "response_model_baseline.json"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_EXCLUDE_DIR_PARTS = {"__pycache__", ".venv", "tests", "scratch", "tmp"}


@dataclass
class Route:
    file: str
    line: int
    method: str
    func: str
    has_response_model: bool
    has_return_annotation: bool
    # Source text of the declared model when it is too weak to count (ADR 0023's
    # `weak_response_model`), else None. A weak model does NOT set
    # has_response_model, so it cannot move the ratchet.
    weak_model: str | None = None


def _iter_files() -> list[Path]:
    out = []
    if not _ROUTES_DIR.is_dir():
        return out
    for p in _ROUTES_DIR.rglob("*.py"):
        parts = p.relative_to(_WORKSHOP).parts
        if any(part in _EXCLUDE_DIR_PARTS for part in parts):
            continue
        out.append(p)
    return out


def _route_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.Call]]:
    """(http_method, decorator_call) for every route-shaped decorator on this
    function -- `@router.get(...)`, `@ingest_router.post(...)`, `@app.put(...)`,
    any `<name>.<verb>(...)` call, since this repo uses more than one router
    variable name (`router`, `ingest_router`) and a hardcoded single name
    would silently miss the others."""
    out = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        f = dec.func
        if not isinstance(f, ast.Attribute):
            continue
        if f.attr in _HTTP_METHODS:
            out.append((f.attr, dec))
    return out


# ---- weak-model detection (ADR 0023, the `weak_response_model` finding) ------
#
# ADR 0023: "A return annotation of `dict`, `list`, `Any` or nothing is not a
# response model and does not count. The declared model must be a pydantic model
# or a `Page[T]` of one."
#
# WHY THIS EXISTS. Until 2026-08-17 this script asked only whether the
# `response_model` KEYWORD was present and never looked at its value. So
# `response_model=dict` satisfied the ratchet, moved the number in the right
# direction, and produced `unknown` in `frontend/src/api/schema.gen.ts` -- which
# is the exact outcome clause 6 exists to prevent. It was the cheapest possible
# way to make the metric improve while the contract stayed empty, and a metric
# that can be satisfied without doing the work is worse than no metric, because
# it reports success.
#
# A parameterised generic over a REAL model is not weak: `list[Project]` and
# `dict[str, MetricLatest]` both generate precise types. Only an untyped
# container or an explicit opt-out is. `response_model=None` counts as weak
# rather than absent because it is FastAPI's explicit "do not build a model".
_WEAK_NAMES = {"dict", "list", "tuple", "set", "Any", "object", "None"}


_BODILESS_STATUSES = {204, 205, 304}


def _status_is_bodiless(dec: ast.Call) -> bool:
    for kw in dec.keywords:
        if isinstance(kw, ast.keyword) and kw.arg == "status_code":
            v = kw.value
            if isinstance(v, ast.Constant) and v.value in _BODILESS_STATUSES:
                return True
    return False


def _render(node) -> str:
    """Best-effort source text for a decorator argument, for the report."""
    if node is None:
        return "<none>"
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _is_weak_model(node) -> bool:
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in _WEAK_NAMES
    if isinstance(node, ast.Attribute):          # typing.Any, t.Any
        return node.attr in _WEAK_NAMES
    if isinstance(node, ast.Subscript):
        # dict[str, Any] is weak; dict[str, MetricLatest] is not. Judge the
        # ELEMENTS, since the container alone says nothing.
        sl = node.slice
        elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        return any(_is_weak_model(e) for e in elts)
    return False


def scan_file(path: Path) -> list[Route]:
    rel = path.relative_to(_WORKSHOP).as_posix()
    try:
        tree = ast.parse(path.read_text(errors="replace"), filename=rel)
    except SyntaxError:
        return []
    out: list[Route] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for method, dec in _route_decorators(node):
            rm_value = None
            for kw in dec.keywords:
                if isinstance(kw, ast.keyword) and kw.arg == "response_model":
                    rm_value = kw.value
                    break
            # `response_model=None` on a 204/205/304 is CORRECT, not weak: those
            # statuses carry no body, so there is no model to declare and
            # FastAPI needs the explicit None to stop inferring one from the
            # `-> None` annotation. Treating it as a violation would put two
            # honest routes permanently in the report, and a gate that cries
            # wolf is a gate someone disables.
            no_body = _status_is_bodiless(dec)
            weak = (
                rm_value is not None
                and _is_weak_model(rm_value)
                and not (no_body and isinstance(rm_value, ast.Constant)
                         and rm_value.value is None)
            )
            has_rm = rm_value is not None and not weak
            out.append(Route(
                file=rel, line=node.lineno, method=method, func=node.name,
                has_response_model=has_rm,
                has_return_annotation=node.returns is not None,
                weak_model=(_render(rm_value) if weak else None),
            ))
    return out


def measure() -> dict:
    files = _iter_files()
    routes: list[Route] = []
    for p in files:
        routes.extend(scan_file(p))

    per_file_without: dict[str, int] = {}
    for r in routes:
        if not r.has_response_model:
            per_file_without[r.file] = per_file_without.get(r.file, 0) + 1

    with_rm = sum(1 for r in routes if r.has_response_model)
    no_return_annotation = sum(1 for r in routes if not r.has_return_annotation)
    weak = [
        {"file": r.file, "line": r.line, "func": r.func, "model": r.weak_model}
        for r in routes if r.weak_model is not None
    ]

    return {
        "scanned_files": len(files),
        "total_routes": len(routes),
        "with_response_model": with_rm,
        "without_response_model": len(routes) - with_rm,
        "no_return_annotation": no_return_annotation,
        "weak_response_model_count": len(weak),
        "weak_response_models": weak,
        "per_file_without_response_model": per_file_without,
    }


def _changed_files(rev: str) -> set[str]:
    """Mirrors check_one_definition.py's `_changed_files`: tracked diff +
    staged diff + untracked, relative to the repo root."""
    out: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only", rev],
        ["git", "diff", "--cached", "--name-only", rev],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            res = subprocess.run(cmd, cwd=_WORKSHOP, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        out.update(l.strip() for l in res.stdout.splitlines() if l.strip())
    return out


def _regressions(current: dict, baseline: dict) -> list[tuple[str, int, int]]:
    """(file, baseline_without, current_without) for every file whose
    without-response_model count went UP. A file absent from the baseline
    (new route file) is not a regression by itself -- there is nothing to
    compare it to yet; it shows up as a regression on its NEXT change once a
    baseline exists for it."""
    cur_per_file = current["per_file_without_response_model"]
    base_per_file = baseline.get("per_file_without_response_model", {})
    out = []
    for f, cur_n in cur_per_file.items():
        base_n = base_per_file.get(f)
        if base_n is not None and cur_n > base_n:
            out.append((f, base_n, cur_n))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--only-changed", metavar="REV", default=None,
                     help="only FAIL for regressions in route files changed since REV; "
                          "still prints every regression found")
    args = ap.parse_args()

    current = measure()
    baseline_path = Path(args.baseline)

    if args.write_baseline:
        pinned = {
            "total_routes": current["total_routes"],
            "with_response_model": current["with_response_model"],
            "without_response_model": current["without_response_model"],
            "per_file_without_response_model": current["per_file_without_response_model"],
        }
        baseline_path.write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n")
        print(f"check_response_model: wrote baseline to {baseline_path} "
              f"({current['total_routes']} routes, {current['with_response_model']} "
              f"with response_model)")
        return 0

    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        baseline = {}

    regressions = _regressions(current, baseline) if baseline else []

    if args.only_changed is not None:
        changed = _changed_files(args.only_changed)
        gating = [r for r in regressions if r[0] in changed]
    else:
        gating = regressions

    if args.json:
        print(json.dumps({
            "current": current,
            "baseline": baseline,
            "regressions": [{"file": f, "baseline": b, "current": c} for f, b, c in regressions],
            "gating_regressions": [{"file": f, "baseline": b, "current": c} for f, b, c in gating],
        }, indent=2, sort_keys=False))
        return 1 if gating else 0

    print(f"check_response_model: {current['scanned_files']} route file(s), "
          f"{current['total_routes']} route(s) scanned")
    print(f"    with response_model:    {current['with_response_model']}")
    print(f"    without response_model: {current['without_response_model']}")
    print(f"    no return annotation:   {current['no_return_annotation']}  (informational only)")
    if current.get("weak_response_model_count"):
        print(f"    WEAK response_model:    {current['weak_response_model_count']}"
              "  (declared but untyped; counted as WITHOUT, per ADR 0023)")
        for w in current["weak_response_models"]:
            print(f"        {w['file']}:{w['line']}  {w['func']}  ->  {w['model']}")

    if not baseline:
        print(f"\n    no baseline at {baseline_path} -- run --write-baseline first")
        return 0

    if regressions:
        print(f"\n=== {len(regressions)} route file(s) got WORSE (more undeclared "
              f"response_model routes) than the pinned baseline")
        for f, b, c in regressions:
            mark = " <- GATING (touches the current diff)" if (f, b, c) in gating else ""
            print(f"    {f}: {b} -> {c}{mark}")
        if args.only_changed is not None:
            if gating:
                print(f"\ncheck_response_model: {len(gating)} of those touch a file "
                      f"changed since {args.only_changed} -- add response_model= to the "
                      f"new/changed route(s) before this lands.")
            else:
                print(f"\ncheck_response_model: none of the {len(regressions)} "
                      f"regression(s) touch a file changed since {args.only_changed}; "
                      f"not blocking this diff for debt it didn't introduce.")
    else:
        print("\ncheck_response_model: no regression against the pinned baseline")

    return 1 if gating else 0


if __name__ == "__main__":
    raise SystemExit(main())
