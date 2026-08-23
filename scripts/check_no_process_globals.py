#!/usr/bin/env python3
"""Process-local mutable global state, ratcheted. Enforces the tenancy clause
in `docs/platform-redesign/target-architecture-2026-08-17.md` §2.6 item 3.

THE FINDING THIS PROTECTS. Measured 2026-08-17: 33 module-level mutable
globals across `backend/` -- the live turn registry (`build_streaming.TURNS`),
the app-state map mutated from a worker thread and read from the request
thread (`app_runner._STATE` / `_LOCKS` / `_CSTATE`), two independent voice/PID
maps, and `chat_store`'s globals-as-function-attributes used as an
out-of-band return channel. This is the hard blocker on running more than one
uvicorn worker: a second process gets its own copy of every one of these, so
two workers silently diverge the moment either one is touched. The plan
doc's own words: "Two workers would silently diverge, and `_recent_hits`
documents that it depends on single-worker uvicorn."

WHAT COUNTS AS A "PROCESS GLOBAL" HERE. Not "any module-level variable with a
mutable TYPE" -- a dict literal that is built once at import time and only
ever READ afterward is a constant lookup table, not shared mutable state, and
flagging it would bury the 33 real ones in noise from every frozen config dict
in the tree. What actually makes a global a piece of process state is that
something, somewhere, changes it after import: either

  (a) a function does `global NAME` and reassigns it (this is how the two
      known SCALAR true positives work: `build_streaming.SHUTTING_DOWN` and
      `birds_analyze._migrated` are plain booleans, not containers, flipped by
      `global` inside a function -- a rebound flag is exactly as
      process-local as a mutated dict), or
  (b) the module-level name is a dict/list/set and something mutates its
      CONTENTS in place -- `NAME[key] = ...`, `NAME.append(...)`,
      `NAME.update(...)`, `del NAME[key]`, etc. -- which needs no `global`
      declaration in Python because the name itself is never rebound, only
      read to get the object.

A declaration with neither kind of evidence anywhere in the file is left
alone. That is what keeps frozensets, tuples, compiled regexes, `Path`
objects, ints, strings, and read-only dicts out of the report without having
to special-case each type: they simply never accumulate mutation evidence.
Verify this yourself: `learnings.py` module-level dicts like `_STOPWORDS` and
`_SEVERITY_NUM` are real dict literals, and `tasks.py`'s `_PROTECTED_ROOTS` is
a frozenset comprehension -- both files score zero, which is the check's own
regression test (see `--self-test`).

CAVEAT, STATED PLAINLY. Container-mutation evidence is matched by NAME alone
within the file (`NAME[...] =`, `NAME.append(...)`), not full scope
resolution -- a local variable or parameter that happens to share a module
global's name, and is genuinely reassigned/mutated only in its own local
scope, is excluded by checking whether the nearest enclosing function shadows
that name (assigns it, or takes it as a parameter, without a `global`
declaration for it) -- but this is a heuristic, not a type checker. It has been
validated against the known true and false positives named above; a novel
false positive is a bug report, not a reason to distrust the whole ratchet.

WHY A RATCHET, NOT AN ABSOLUTE GATE. 33 of these exist today and deleting
them is the multi-worker item (§2.6), which is real, staged work, not a
one-line fix. Gating on ">0" would fail the reviewer for every task in the
repo starting today -- exactly the "fires constantly, gets disabled" failure
`check_one_definition.py`'s header warns about. So this pins today's count in
`no_process_globals_baseline.json` and fails only when the count goes UP.
Going down is reported, not auto-repinned, for the same reason
`check_module_layout.py` does not auto-repin: this script cannot tell a real
deletion apart from a file temporarily out of scope mid-edit, and a silent
tightening would ratchet against the next contributor for someone else's
work. Re-run `--write-baseline` deliberately once a reduction is confirmed.

Usage:
    python3 scripts/check_no_process_globals.py                # measure, compare, exit 1 on regression
    python3 scripts/check_no_process_globals.py --json
    python3 scripts/check_no_process_globals.py --write-baseline
    python3 scripts/check_no_process_globals.py --self-test     # known true/false positives
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

_WORKSHOP = Path(__file__).resolve().parents[1]
_BACKEND = _WORKSHOP / "backend"
_BASELINE_PATH = Path(__file__).resolve().parent / "no_process_globals_baseline.json"

_EXCLUDE_DIR_PARTS = {
    "__pycache__", ".venv", "node_modules", "tests", "scratch", "tmp", ".git",
}
_EXCLUDE_PATH_PARTS = {"db/migrations/versions"}

# Method/attribute names that mutate a dict/list/set IN PLACE (no rebind, so
# no `global` is needed to reach them from inside a function).
_MUTATOR_METHODS = {
    "append", "extend", "insert", "remove", "pop", "clear", "update",
    "add", "discard", "popitem", "setdefault", "sort", "reverse",
}


def _iter_files(root: Path = _WORKSHOP) -> list[Path]:
    out = []
    for p in (root / "backend").rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        parts = p.relative_to(root).parts
        if any(part in _EXCLUDE_DIR_PARTS for part in parts):
            continue
        if any(ex in rel for ex in _EXCLUDE_PATH_PARTS):
            continue
        out.append(p)
    return out


def _rhs_kind(value: ast.AST) -> str:
    if isinstance(value, ast.UnaryOp) and isinstance(value.operand, ast.Constant):
        return "scalar"
    if isinstance(value, ast.Dict):
        return "dict"
    if isinstance(value, ast.List):
        return "list"
    if isinstance(value, ast.Set):
        return "set"
    if isinstance(value, ast.Constant):
        return "scalar"
    if isinstance(value, ast.Call):
        fname = ""
        f = value.func
        if isinstance(f, ast.Name):
            fname = f.id
        elif isinstance(f, ast.Attribute):
            fname = f.attr
        if fname in ("dict", "defaultdict", "Counter", "OrderedDict"):
            return "dict"
        if fname in ("list", "deque"):
            return "list"
        if fname == "set":
            return "set"
        return f"call:{fname}"
    return type(value).__name__


@dataclass
class Candidate:
    name: str
    file: str
    line: int
    kind: str
    evidence: str = ""  # "global-reassign" | "container-mutate" | "" (none found)


def _module_level_candidates(tree: ast.Module, rel: str) -> dict[str, Candidate]:
    """Names assigned at true module scope (top-level, or nested inside a
    top-level if/try/for/with -- those still execute at import time -- but
    NOT inside any function or class body)."""
    out: dict[str, Candidate] = {}

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if value is None:
                    continue
                for t in targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = Candidate(t.id, rel, node.lineno, _rhs_kind(value))
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.Try)):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    sub = getattr(node, attr, None)
                    if not sub:
                        continue
                    if attr == "handlers":
                        for h in sub:
                            walk(h.body)
                    else:
                        walk(sub)
            elif isinstance(node, ast.With):
                walk(node.body)
            # FunctionDef / AsyncFunctionDef / ClassDef: deliberately not
            # descended into -- those are function locals / class attributes,
            # not module globals.

    walk(tree.body)
    return out


class _FuncScope:
    """What a single function/method shadows: names it declares `global` for
    (definitely reach the module scope) and names it binds locally without a
    matching `global` (definitely do NOT reach the module scope, from code
    inside this function)."""

    def __init__(self, node: ast.AST):
        self.globals: set[str] = set()
        self.locals: set[str] = set()
        self._collect(node, top=True)

    def _bind(self, name: str) -> None:
        if name not in self.globals:
            self.locals.add(name)

    def _collect(self, node: ast.AST, top: bool) -> None:
        # Only walk THIS function's own frame -- stop at nested
        # function/class boundaries so their locals don't leak in as ours,
        # and so our globals don't leak into them (each frame gets its own
        # _FuncScope when the outer walk visits it).
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Global):
                self.globals.update(child.names)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                self._bind(child.id)
            elif isinstance(child, (ast.arg,)):
                self._bind(child.arg)
            self._collect(child, top=False)
        if top:
            args = getattr(node, "args", None)
            if args:
                for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
                          + ([args.vararg] if args.vararg else [])
                          + ([args.kwarg] if args.kwarg else [])):
                    self._bind(a.arg)


def _find_evidence(tree: ast.Module, candidates: dict[str, Candidate]) -> None:
    """Walk the whole file looking for mutation/reassignment evidence for
    each candidate. Each AST node is visited with the nearest-enclosing
    _FuncScope (None at true module level, where every candidate use is
    definitionally the module global itself)."""
    names = set(candidates)
    if not names:
        return

    def resolves_to_module(name: str, scope: "_FuncScope | None") -> bool:
        if scope is None:
            return True
        if name in scope.globals:
            return True
        if name in scope.locals:
            return False
        return True  # free variable: not bound in this frame, assume it reaches outward

    def mark(name: str, kind: str) -> None:
        c = candidates.get(name)
        if c and not c.evidence:
            c.evidence = kind

    def visit(node: ast.AST, scope: "_FuncScope | None") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = _FuncScope(node)
            for stmt in node.body:
                visit(stmt, inner)
            return  # don't also descend generically below

        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target]
            )
            for t in targets:
                if isinstance(t, ast.Name) and t.id in names:
                    if scope is not None and t.id in scope.globals and resolves_to_module(t.id, scope):
                        mark(t.id, "global-reassign")
                elif isinstance(t, ast.Subscript):
                    base = t.value
                    if isinstance(base, ast.Name) and base.id in names and resolves_to_module(base.id, scope):
                        mark(base.id, "container-mutate")
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    if t.value.id in names and resolves_to_module(t.value.id, scope):
                        mark(t.value.id, "container-mutate")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in _MUTATOR_METHODS:
                base = f.value
                if isinstance(base, ast.Name) and base.id in names and resolves_to_module(base.id, scope):
                    mark(base.id, "container-mutate")

        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, None)


def scan_file(path: Path, root: Path = _WORKSHOP) -> list[Candidate]:
    rel = path.relative_to(root).as_posix()
    try:
        tree = ast.parse(path.read_text(errors="replace"), filename=rel)
    except SyntaxError:
        return []
    candidates = _module_level_candidates(tree, rel)
    if not candidates:
        return []
    _find_evidence(tree, candidates)
    return [c for c in candidates.values() if c.evidence]


def measure(root: Path = _WORKSHOP) -> dict:
    """Measure mutable-global count over the backend tree under `root`.

    `root` is a parameter so DELTA MODE can measure the same way over a
    `git archive` extract of the run's base ref.
    """
    files = _iter_files(root)
    hits: list[Candidate] = []
    for p in files:
        hits.extend(scan_file(p, root))
    hits.sort(key=lambda c: (c.file, c.line))
    return {
        "scanned_files": len(files),
        "mutable_global_count": len(hits),
        "mutable_globals": [
            {"name": c.name, "file": c.file, "line": c.line, "kind": c.kind, "evidence": c.evidence}
            for c in hits
        ],
    }


_KNOWN_TRUE_POSITIVES = {
    ("services/build_streaming.py", "TURNS"),
    ("services/build_streaming.py", "SHUTTING_DOWN"),
    ("services/app_runner.py", "_STATE"),
    ("services/app_runner.py", "_LOCKS"),
    ("services/chat_log.py", "_locks"),
    ("services/chat_log.py", "_MIRROR_TASKS"),
    ("services/chat_log.py", "_MIRROR_CHAIN"),
    ("services/chat_log.py", "_rotate_waiting_at"),
    ("services/chat_store.py", "_SUMMARY_CACHE"),
    ("services/knowledge.py", "_list_notes_cache"),
    ("services/birds_analyze.py", "_migrated"),
    ("services/birds_analyze.py", "_centroids_cache"),
}
# Three names from the task brief's true-positive list are DELIBERATELY not
# asserted here: `app_runner._CSTATE`, `birds_analyze._recent_hits`, and
# `chat_context._PLATFORM_PROVIDERS`. Checked by hand (`grep -rn` across all
# of `backend/`, not just the declaring file): none of the three is mutated
# or reassigned ANYWHERE in the current tree -- `_PLATFORM_PROVIDERS` is even
# commented, by its own author, as "a module-level constant". Flagging them
# would mean abandoning "must not flag a dict that is never mutated" for
# these three specifically, which this script declines to do without a
# concrete counter-example. If one of them starts being mutated, this
# detector will catch it the moment that lands.
_KNOWN_ZERO_FILES = {"services/tasks.py", "services/learnings.py"}


def self_test() -> int:
    report = measure()
    found = {(h["file"].removeprefix("backend/"), h["name"]) for h in report["mutable_globals"]}
    ok = True
    missing = _KNOWN_TRUE_POSITIVES - found
    if missing:
        ok = False
        print(f"MISSING known true positives: {sorted(missing)}")
    for f in _KNOWN_ZERO_FILES:
        got = [h for h in report["mutable_globals"] if h["file"].removeprefix("backend/") == f]
        if got:
            ok = False
            print(f"FALSE POSITIVE(S) in {f} (expected zero): {got}")
    if ok:
        print(f"check_no_process_globals --self-test: PASS "
              f"({len(_KNOWN_TRUE_POSITIVES)} known true positives found, "
              f"{len(_KNOWN_ZERO_FILES)} known-clean files stayed clean)")
        return 0
    return 1


_HIGHER_IS_WORSE = {"mutable_global_count": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--self-test", action="store_true",
                     help="check the detector against known true/false positives; exit 1 on mismatch")
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: scan REV's tree as well and fail only on a count that "
                         "got worse between REV and the working tree. The pinned baseline is "
                         "still reported as FYI. See this script's header.")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    current = measure()
    baseline_path = Path(args.baseline)

    if args.write_baseline:
        pinned = {"mutable_global_count": current["mutable_global_count"]}
        baseline_path.write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n")
        print(f"check_no_process_globals: wrote baseline to {baseline_path}: {pinned}")
        return 0

    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        baseline = {}

    # DELTA MODE. `since` becomes the thing regressions are judged against; the
    # pinned baseline is still compared and still printed so inherited debt stays
    # visible even when it is not this run's fault.
    since: dict | None = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, _WORKSHOP) as base_tree:
                since = measure(base_tree)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_no_process_globals: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    judge_against = since if since is not None else baseline
    against = f"the base ref {args.since}" if since is not None else "the pinned baseline"

    regressed = (
        bool(judge_against)
        and current["mutable_global_count"] > judge_against.get("mutable_global_count", 0)
    )

    if args.json:
        payload: dict = {"current": current, "baseline": baseline, "regression": regressed}
        if since is not None:
            payload["since_ref"] = args.since
            payload["since"] = {"mutable_global_count": since["mutable_global_count"]}
        print(json.dumps(payload, indent=2, sort_keys=False))
        return 1 if regressed else 0

    print(f"check_no_process_globals: {current['scanned_files']} backend .py files scanned")
    print(f"    mutable process globals: {current['mutable_global_count']}")
    for h in current["mutable_globals"]:
        print(f"        {h['name']:24} {h['file']}:{h['line']}  ({h['kind']}, {h['evidence']})")

    if not judge_against:
        print(f"\n    no baseline at {baseline_path} -- run --write-baseline first")
        return 0

    base_n = judge_against.get("mutable_global_count", 0)
    if regressed:
        print(f"\ncheck_no_process_globals: REGRESSION against {against} -- "
              f"{base_n} -> {current['mutable_global_count']}. "
              "A new process-local mutable global blocks running more than one uvicorn worker "
              "(target-architecture-2026-08-17.md §2.6). Move the state into the request/session "
              "scope, or if it is a genuine constant, remove the mutation that triggered this.")
    elif current["mutable_global_count"] < base_n:
        print(f"\ncheck_no_process_globals: improved against {against} -- "
              f"{base_n} -> {current['mutable_global_count']}. "
              "Not auto-repinned (see header); run --write-baseline to lock it in.")
    else:
        print(f"\ncheck_no_process_globals: no regression against {against}")

    # In delta mode, also report any gap between the working tree and the pinned
    # baseline -- inherited debt stays visible even when it is not this run's fault.
    if since is not None and baseline:
        pin_n = baseline.get("mutable_global_count", 0)
        if current["mutable_global_count"] > pin_n:
            print(f"    FYI mutable_global_count: pinned {pin_n} -> now "
                  f"{current['mutable_global_count']} (inherited from base ref, not gating)")

    return 1 if regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
