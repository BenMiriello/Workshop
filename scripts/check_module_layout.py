#!/usr/bin/env python3
"""Backend module layout, ratcheted. ADR 0024 (module layout / domain packages),
derived from `docs/platform-redesign/target-architecture-2026-08-17.md` §2.2 --
read that section for the narrative; this script is the mechanical half of it.

THE FINDING THIS PROTECTS. `services/` is 116 flat files with 22 obvious prefix
clusters (`chat_*` x11, `ticket_*` x4, `deploy*` x4, ...) and no directories to
match. The measured consequence is a near-1:1 count of function-local imports
against module-scope ones: in most mutual A<->B pairs, one direction has been
pushed into a function body specifically to dodge a circular `ImportError` at
import time. That is not a style choice, it is scar tissue -- visible proof of
a cycle the flat namespace makes impossible to avoid without it. Removing every
upward layering violation still leaves a single strongly-connected component of
dozens of modules entirely inside `services/`, which is what makes the flat
namespace the disease and the layering violations merely a symptom.

FOUR METRICS, EACH RATCHETED SEPARATELY:

1. function_local_imports / module_scope_imports -- every NAME imported from
   another backend module (`from services import chat_store, chat_context`
   counts as two, not one -- each is a separate intra-backend dependency),
   split by whether the import sits inside a function/method body or at
   module scope. A rising function-local count with a flat or falling
   module-scope count is the cycle-workaround pattern actively getting worse.
2. import_cycles -- strongly-connected components (Tarjan) in the backend
   module import graph (edges = resolved intra-backend imports, BOTH
   module-scope and function-local, because a deferred import is still a real
   runtime dependency -- excluding it would make the workaround look like a
   fix). Reported as the number of SCCs with >=2 modules and the size of the
   largest one.
3. upward_layering_violations -- edges that run against the target dependency
   direction. The target names from the plan doc are `api -> domains ->
   platform -> db`; today's tree has no `domains/` or `platform/` split yet,
   so this checks it in terms of the CURRENT three directories that exist,
   `backend/routes/` (api) -> `backend/services/` (domains, for now) ->
   `backend/db/` (db), which is the plan doc's own mapping. A violation is an
   edge whose source directory is deeper in that chain than its target
   (`services/` importing `routes/`, `db/` importing `services/` or `routes/`).
   `routes/` importing `db/` directly (skipping `services/`) is not counted
   here -- it is a different smell (a thin-router violation), not a reversed
   dependency, and this script is not the place for it.
4. big_files -- `.py` files in `backend/` over `_BIG_FILE_LINES` lines. The
   plan doc's ADR-in-progress framing is "a god module is a reviewable event";
   this is the mechanical trigger for that review, not a line-count opinion.

WHY A RATCHET, NOT AN ABSOLUTE GATE. Every one of these four numbers is
nonzero today, some of them badly so (one SCC alone is 27% of the backend).
Gating on the absolute value would fail the reviewer for every unrelated task
in the repo starting today, which is exactly the failure mode
`check_one_definition.py`'s header warns about: "a checker that fires
constantly gets disabled." So this script pins today's measurement in
`module_layout_baseline.json` and fails only when a metric gets WORSE than its
pinned value. Getting better is reported but NOT auto-repinned: unlike a
purely additive baseline (`field_occupancy.py`'s field-name set), these
numbers can move for reasons this script cannot distinguish from real
progress -- a module getting deleted, a test-only file moving in or out of
scope, a concurrent edit landing mid-measurement. Auto-tightening on a number
this script cannot fully explain would silently ratchet against the NEXT
contributor for someone else's work. A human (or an agent that just did the
work) re-runs `--write-baseline` deliberately once the improvement is
confirmed, the same explicit step `field_occupancy.py` already requires.

Usage:
    python3 scripts/check_module_layout.py                # measure, compare, exit 1 on regression
    python3 scripts/check_module_layout.py --json
    python3 scripts/check_module_layout.py --write-baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_BACKEND = _WORKSHOP / "backend"
_BASELINE_PATH = Path(__file__).resolve().parent / "module_layout_baseline.json"

_EXCLUDE_DIR_PARTS = {
    "__pycache__", ".venv", "node_modules", "tests", "scratch", "tmp", ".git",
}
_EXCLUDE_PATH_PARTS = {"db/migrations/versions"}

_BIG_FILE_LINES = 800

# The three CURRENT directories the layering rule is checked against, in
# target dependency order (lower index = closer to the api/edge; a module may
# only import from a directory at the SAME or a HIGHER index). This is today's
# `routes/ -> services/ -> db/` chain; under the target names in the plan doc
# `routes/` is `api/`, `services/` is (for now) `domains/`, and `db/` stays
# `db/` -- `platform/` does not exist yet as a directory, so it is not in this
# list, and nothing is checked against it.
_LAYER_ORDER = {"routes": 0, "services": 1, "db": 2}

import ast  # noqa: E402


def _iter_files() -> list[Path]:
    out = []
    for p in _BACKEND.rglob("*.py"):
        rel = p.relative_to(_WORKSHOP).as_posix()
        parts = p.relative_to(_WORKSHOP).parts
        if any(part in _EXCLUDE_DIR_PARTS for part in parts):
            continue
        if any(ex in rel for ex in _EXCLUDE_PATH_PARTS):
            continue
        out.append(p)
    return out


def _modname(p: Path) -> str:
    """Dotted module path relative to `backend/`, e.g.
    `backend/services/tasks.py` -> `services.tasks`."""
    rel = p.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _owner_pkg(p: Path) -> list[str]:
    """Dotted package parts of the directory a file lives in, for resolving
    relative imports (`from . import x`, `from .. import y`)."""
    rel = p.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    return parts[:-1]


class _ModuleGraph:
    def __init__(self, files: list[Path]):
        self.files = files
        self.modname_by_path = {p: _modname(p) for p in files}
        self.all_modules = set(self.modname_by_path.values())
        pkg_names: set[str] = set()
        for m in self.all_modules:
            parts = m.split(".")
            for i in range(1, len(parts)):
                pkg_names.add(".".join(parts[:i]))
        self.pkg_names = pkg_names
        self.resolvable = self.all_modules | pkg_names
        # edges: dict[src_module] -> set[target_module], deduplicated
        self.edges: dict[str, set[str]] = {}
        self.func_local_imports = 0
        self.module_scope_imports = 0
        self._build()

    def _is_internal(self, modpath: str) -> bool:
        return bool(modpath) and modpath in self.resolvable

    def _add_edge(self, src: str, target: str) -> None:
        if target and target != src:
            self.edges.setdefault(src, set()).add(target)

    def _build(self) -> None:
        for p in self.files:
            src_mod = self.modname_by_path[p]
            rel = p.relative_to(_WORKSHOP).as_posix()
            try:
                tree = ast.parse(p.read_text(errors="replace"), filename=rel)
            except SyntaxError:
                continue
            func_stack: list[ast.AST] = []
            graph = self

            class _V(ast.NodeVisitor):
                def visit_FunctionDef(self, node):  # noqa: N802
                    func_stack.append(node)
                    self.generic_visit(node)
                    func_stack.pop()

                visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

                def visit_Import(self, node):  # noqa: N802
                    hits = 0
                    for alias in node.names:
                        if graph._is_internal(alias.name):
                            hits += 1
                            graph._add_edge(src_mod, alias.name)
                    self._tally(hits)

                def visit_ImportFrom(self, node):  # noqa: N802
                    if node.level and node.level > 0:
                        pkg_parts = _owner_pkg(p)
                        base_parts = (
                            pkg_parts[: len(pkg_parts) - (node.level - 1)]
                            if node.level > 1 else pkg_parts
                        )
                        base = ".".join(base_parts)
                        full = (base + "." + node.module) if node.module else base
                        full = full.strip(".")
                        # Prefer the specific submodule an alias names (`from .
                        # import chat_context` inside services/ -> the edge is
                        # to services.chat_context, not the services package)
                        # over the bare package, for the same reason as the
                        # absolute-import branch below.
                        target_hits = 0
                        for alias in node.names:
                            candidate = f"{full}.{alias.name}" if full else alias.name
                            if candidate in graph.all_modules:
                                graph._add_edge(src_mod, candidate)
                                target_hits += 1
                        if not target_hits:
                            graph._add_edge(src_mod, full or base)
                            target_hits = len(node.names)
                        self._tally(target_hits)
                        return
                    modpath = node.module or ""
                    hits = 0
                    # A bare package name (`from services import chat_context`)
                    # ALWAYS resolves as "internal" via the pkg_names prefix set,
                    # but recording the edge as the generic package flattens
                    # every submodule-to-submodule dependency onto one node and
                    # hides the real cycle. So: resolve each alias to the
                    # SPECIFIC submodule first when one exists, and only fall
                    # back to the bare module/package if no alias names one
                    # (e.g. `from services.chat_store import get_thread`, where
                    # `chat_store` is a function/class, not a submodule).
                    for alias in node.names:
                        full = f"{modpath}.{alias.name}" if modpath else alias.name
                        if full in graph.all_modules:
                            hits += 1
                            graph._add_edge(src_mod, full)
                    if not hits and graph._is_internal(modpath):
                        hits = len(node.names)
                        graph._add_edge(src_mod, modpath)
                    self._tally(hits)

                def _tally(self, hits: int) -> None:
                    # Counted per NAME imported, not per statement: `from
                    # services import chat_store, chat_context, chat_log` is
                    # three internal dependencies created in one line, and
                    # collapsing them to "one import statement" understates the
                    # coupling by exactly the amount that matters here. This is
                    # also what reproduces the plan doc's own count (~438
                    # function-local / ~430 module-scope) most closely.
                    if not hits:
                        return
                    if func_stack:
                        graph.func_local_imports += hits
                    else:
                        graph.module_scope_imports += hits

            _V().visit(tree)


def _tarjan_sccs(edges: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan's SCC over the module graph. Iterative to avoid
    recursion-depth issues on a graph this size (~200+ nodes)."""
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    result: list[list[str]] = []

    nodes = set(edges.keys())
    for tgts in edges.values():
        nodes.update(tgts)

    def strongconnect(v: str) -> None:
        work = [(v, iter(edges.get(v, ())))]
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True

        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = index_counter[0]
                    lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(edges.get(w, ()))))
                    advanced = True
                    break
                elif on_stack.get(w):
                    lowlink[node] = min(lowlink[node], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                result.append(comp)

    for v in sorted(nodes):
        if v not in index:
            strongconnect(v)
    return result


def _layer_of(modname: str) -> str | None:
    top = modname.split(".")[0]
    return top if top in _LAYER_ORDER else None


def _upward_violations(edges: dict[str, set[str]]) -> list[tuple[str, str]]:
    out = []
    for src, targets in edges.items():
        src_layer = _layer_of(src)
        if src_layer is None:
            continue
        for tgt in targets:
            tgt_layer = _layer_of(tgt)
            if tgt_layer is None:
                continue
            if _LAYER_ORDER[src_layer] > _LAYER_ORDER[tgt_layer]:
                out.append((src, tgt))
    return sorted(out)


def _big_files(files: list[Path]) -> list[tuple[str, int]]:
    out = []
    for p in files:
        try:
            n = sum(1 for _ in p.open(errors="replace"))
        except OSError:
            continue
        if n > _BIG_FILE_LINES:
            out.append((p.relative_to(_WORKSHOP).as_posix(), n))
    return sorted(out, key=lambda t: -t[1])


def measure() -> dict:
    files = _iter_files()
    graph = _ModuleGraph(files)
    sccs = _tarjan_sccs(graph.edges)
    multi_sccs = sorted((s for s in sccs if len(s) >= 2), key=len, reverse=True)
    violations = _upward_violations(graph.edges)
    big = _big_files(files)

    return {
        "scanned_files": len(files),
        "function_local_imports": graph.func_local_imports,
        "module_scope_imports": graph.module_scope_imports,
        "scc_count": len(multi_sccs),
        "largest_scc_size": len(multi_sccs[0]) if multi_sccs else 0,
        "largest_scc_modules": sorted(multi_sccs[0]) if multi_sccs else [],
        "upward_violations": len(violations),
        "upward_violation_edges": [f"{s} -> {t}" for s, t in violations],
        "big_file_count": len(big),
        "big_files": [{"file": f, "lines": n} for f, n in big],
    }


# Metrics where a HIGHER number is worse (every metric here, currently). Kept
# as an explicit map rather than assumed, so a future metric with the opposite
# sense (e.g. "% of routers under the thin-router threshold", where higher is
# better) does not silently invert.
_HIGHER_IS_WORSE = {
    "function_local_imports": True,
    "scc_count": True,
    "largest_scc_size": True,
    "upward_violations": True,
    "big_file_count": True,
}


def _regressions(current: dict, baseline: dict) -> list[str]:
    out = []
    for key, higher_is_worse in _HIGHER_IS_WORSE.items():
        if key not in baseline:
            continue  # a metric added after this baseline was pinned: nothing to compare
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            continue
        worse = (cur > base) if higher_is_worse else (cur < base)
        if worse:
            out.append(f"{key}: {base} -> {cur}")
    return out


def _improvements(current: dict, baseline: dict) -> list[str]:
    out = []
    for key, higher_is_worse in _HIGHER_IS_WORSE.items():
        if key not in baseline:
            continue
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            continue
        better = (cur < base) if higher_is_worse else (cur > base)
        if better:
            out.append(f"{key}: {base} -> {cur}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true",
                     help="record current metrics as the new pinned baseline and exit")
    args = ap.parse_args()

    current = measure()
    baseline_path = Path(args.baseline)

    if args.write_baseline:
        pinned = {k: current[k] for k in _HIGHER_IS_WORSE}
        pinned["_measured_against"] = current["scanned_files"]
        baseline_path.write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n")
        print(f"check_module_layout: wrote baseline to {baseline_path}: "
              f"{ {k: pinned[k] for k in _HIGHER_IS_WORSE} }")
        return 0

    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        baseline = {}

    regressions = _regressions(current, baseline) if baseline else []
    improvements = _improvements(current, baseline) if baseline else []

    if args.json:
        print(json.dumps({
            "current": current,
            "baseline": baseline,
            "regressions": regressions,
            "improvements": improvements,
        }, indent=2, sort_keys=False))
        return 1 if regressions else 0

    print("check_module_layout:")
    print(f"    {current['scanned_files']} backend .py files scanned")
    print(f"    function-local imports: {current['function_local_imports']}   "
          f"module-scope imports: {current['module_scope_imports']}")
    print(f"    import cycles: {current['scc_count']} SCC(s) of size >= 2, "
          f"largest = {current['largest_scc_size']} module(s)")
    print(f"    upward layering violations (routes/services/db): "
          f"{current['upward_violations']}")
    print(f"    files over {_BIG_FILE_LINES} lines: {current['big_file_count']}")

    if not baseline:
        print(f"\n    no baseline at {baseline_path} -- run --write-baseline first")
        return 0

    if regressions:
        print(f"\n=== {len(regressions)} metric(s) regressed past the pinned baseline")
        for r in regressions:
            print(f"    {r}")
        print("\ncheck_module_layout: this is worse than the pinned tree. Either "
              "undo the regression, or if it is a deliberate, understood tradeoff, "
              "re-run with --write-baseline after review.")
    else:
        print("\ncheck_module_layout: no regression against the pinned baseline")

    if improvements:
        print(f"\n=== {len(improvements)} metric(s) improved (not auto-repinned -- "
              f"see this script's header for why)")
        for i in improvements:
            print(f"    {i}")
        print("    Run --write-baseline to lock in the improvement once confirmed.")

    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
