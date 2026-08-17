#!/usr/bin/env python3
"""One definition per enum, enforced. R3 in
docs/platform-redesign/coherence-overhaul-2026-08-16.md.

The premise, measured on 2026-08-16: the autonomy enum
(reactive/supervised/autonomous) was declared FOUR times, agent roles twice,
EFFORTS twice, and CHAT_MODES twice with a DIFFERENT element order in each
copy -- in a domain whose canonical file already carries a comment warning
that exactly this drift happened once before. "Six mappers with two already
drifted is not a discipline problem; it is a missing test."

WHAT THIS DOES. Scans `backend/` (Python) and `frontend/src` + `packages/ui/src`
(TypeScript) for status/state/kind/mode/role/effort/autonomy/priority/category/
tier/type value-set declarations: a module-level constant assigned a tuple,
list, or `type X = 'a' | 'b'` union of >=3 string literals, whose NAME hints
that it is a closed vocabulary (see `_NAME_HINT_RE`). Two declarations with the
exact same set of values -- regardless of name, order, or language -- are a
duplicate, found the same way `CHAT_MODES` was found to disagree: by value,
not by name, because a rename never shows up in a `grep` for the old name.

WHAT THIS DELIBERATELY DOES NOT DO: hardcode the list of known duplicates.
Another agent may be consolidating some of these in parallel with this one, so
a hardcoded list would go stale within the session that wrote it. This script
re-derives its findings from the tree every run.

WHY IT DOES NOT GATE ON EVERY PRE-EXISTING DUPLICATE. Running this
unconditionally against the whole tree would fail the reviewer gate for every
task in the repo until every one of the known duplicates above is
consolidated -- "a checker that fires constantly gets disabled, which is how
we got here" (R3's own text). So `--only-changed <rev>` restricts FAILURE to
duplicate groups where at least one declaring file is in the diff: touching
either side of an existing duplicate, or introducing a new one, keeps you
honest; a duplicate sitting untouched in a file nobody is looking at does not
block unrelated work. It still PRINTS every duplicate group either way, so the
debt stays visible.

Usage:
    python3 scripts/check_one_definition.py                # full scan, report + exit 1 if any dup
    python3 scripts/check_one_definition.py --json
    python3 scripts/check_one_definition.py --only-changed HEAD   # gate mode
    python3 scripts/check_one_definition.py --only-changed HEAD --json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]

# Directories never worth scanning: vendored/build output, caches, and tests
# (a test file legitimately re-lists a small value set to parametrize against,
# and that is not the drift this script exists to catch -- the drift is two
# PRODUCTION modules disagreeing about the vocabulary).
_EXCLUDE_DIR_PARTS = {
    "node_modules", ".venv", "__pycache__", "dist", "build", ".git",
    "coverage", ".next", "tests", "scratch", "tmp",
}
# Migrations are a historical record of what a schema WAS; a literal frozen in
# a migration file is not a live second definition to reconcile.
_EXCLUDE_PATH_PARTS = {"db/migrations/versions"}

_PY_ROOTS = [_WORKSHOP / "backend"]
_TS_ROOTS = [_WORKSHOP / "frontend" / "src", _WORKSHOP / "packages" / "ui" / "src"]

# A constant name has to look like a closed vocabulary before its value-set is
# even a candidate. This is what keeps `SYNCABLE_FIELDS` (a list of ticket
# FIELD NAMES that legitimately overlaps another list of field names) and
# `_TRACKED` / `_HISTORIC` out of the report: they don't name a status, state,
# kind, mode, role, effort, autonomy, priority, category, tier or type.
_NAME_HINT_RE = re.compile(
    r"(STATUS|STATE|KIND|MODE|ROLE|EFFORT|AUTONOMY|PRIORIT|CATEGOR|TIER|TYPE)",
    re.IGNORECASE,
)

# Below this many members, a shared value-set is too likely to be a
# coincidence (`("asc", "desc")`) rather than a real closed vocabulary. Every
# named live duplicate (autonomy, roles, efforts, chat modes) has >= 3.
_MIN_MEMBERS = 3


@dataclass
class Decl:
    values: frozenset
    name: str
    file: str  # repo-relative
    line: int
    lang: str  # "py" | "ts"


def _iter_files(roots: list[Path], suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix not in suffixes:
                continue
            rel_parts = p.relative_to(_WORKSHOP).parts
            if any(part in _EXCLUDE_DIR_PARTS for part in rel_parts):
                continue
            rel = p.relative_to(_WORKSHOP).as_posix()
            if any(ex in rel for ex in _EXCLUDE_PATH_PARTS):
                continue
            if rel.startswith("scripts/"):  # this tool's own fixtures/tests
                continue
            out.append(p)
    return out


def _py_string_literal_set(node: ast.AST) -> frozenset | None:
    """The frozenset of string values if `node` is a Tuple/List/Set literal of
    ONLY string constants, else None."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    values = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            values.append(elt.value)
        else:
            return None  # a non-string element means this isn't a plain enum
    return frozenset(values)


def scan_python(paths: list[Path]) -> list[Decl]:
    out: list[Decl] = []
    for path in paths:
        rel = path.relative_to(_WORKSHOP).as_posix()
        try:
            tree = ast.parse(path.read_text(errors="replace"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                continue
            values = _py_string_literal_set(value)
            if values is None or len(values) < _MIN_MEMBERS:
                continue
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if not _NAME_HINT_RE.search(t.id):
                    continue
                out.append(Decl(values, t.id, rel, node.lineno, "py"))
    return out


# `type Foo = 'a' | 'b' | 'c';` (a string-literal union type).
_TS_UNION_RE = re.compile(
    r"^\s*(?:export\s+)?type\s+(\w+)\s*=\s*((?:'[^']*'|\"[^\"]*\")"
    r"(?:\s*\|\s*(?:'[^']*'|\"[^\"]*\"))+)\s*;?\s*$"
)
# `const FOO = ['a', 'b', 'c']` / `const FOO: X[] = [...] as const` — array of
# plain string literals only, on one line (the common style in this repo).
_TS_ARRAY_RE = re.compile(
    r"^\s*(?:export\s+)?const\s+(\w+)\s*(?::\s*[^=]+)?=\s*\["
    r"((?:'[^']*'|\"[^\"]*\")(?:\s*,\s*(?:'[^']*'|\"[^\"]*\"))*)\s*,?\s*\]"
)
_TS_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def scan_typescript(paths: list[Path]) -> list[Decl]:
    out: list[Decl] = []
    for path in paths:
        rel = path.relative_to(_WORKSHOP).as_posix()
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for rx in (_TS_UNION_RE, _TS_ARRAY_RE):
                m = rx.match(line)
                if not m:
                    continue
                name, body = m.group(1), m.group(2)
                if not _NAME_HINT_RE.search(name):
                    continue
                vals = frozenset(
                    a or b for a, b in _TS_STRING_RE.findall(body)
                )
                if len(vals) < _MIN_MEMBERS:
                    continue
                out.append(Decl(vals, name, rel, i, "ts"))
    return out


@dataclass
class DupGroup:
    values: frozenset
    decls: list[Decl] = field(default_factory=list)


def find_duplicates(decls: list[Decl]) -> list[DupGroup]:
    """Group declarations by exact value-set, then keep only groups that
    contain a REAL duplicate: two-or-more declarations of the SAME language
    (both Python, or both TypeScript) in different files.

    A single Python constant paired with a single TypeScript type of the same
    name/values is not drift -- it is the ordinary shape of a typed API
    contract (TS cannot import a Python module, so the frontend type is a
    deliberate, necessary second copy, not a second SOURCE OF TRUTH). Without
    this filter almost every backend enum with a hand-written frontend mirror
    would be flagged, which is exactly the "fires constantly, gets disabled"
    failure mode R3 warns against. A same-language pair (two Python modules,
    or two TypeScript files) IS the drift this script exists to catch: within
    one language, one of the two could simply import the other.
    """
    by_values: dict[frozenset, list[Decl]] = {}
    for d in decls:
        by_values.setdefault(d.values, []).append(d)
    groups = []
    for values, ds in by_values.items():
        by_lang: dict[str, set[str]] = {}
        for d in ds:
            by_lang.setdefault(d.lang, set()).add(d.file)
        if any(len(files) >= 2 for files in by_lang.values()):
            groups.append(DupGroup(values, sorted(ds, key=lambda d: (d.file, d.line))))
    groups.sort(key=lambda g: (-len(g.decls), sorted(g.values)))
    return groups


def _changed_files(rev: str) -> set[str]:
    """Mirror reviewer.sh's CHANGED_ALL: tracked diff + staged diff + untracked,
    relative to the repo root."""
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only-changed", metavar="REV", default=None,
                     help="only FAIL for duplicate groups touching a file changed "
                          "since REV; still prints every group found")
    args = ap.parse_args()

    py_files = _iter_files(_PY_ROOTS, (".py",))
    ts_files = _iter_files(_TS_ROOTS, (".ts", ".tsx"))
    decls = scan_python(py_files) + scan_typescript(ts_files)
    groups = find_duplicates(decls)

    if args.only_changed is not None:
        changed = _changed_files(args.only_changed)
        gating = [g for g in groups if any(d.file in changed for d in g.decls)]
    else:
        gating = groups

    if args.json:
        def _g(g: DupGroup) -> dict:
            return {
                "values": sorted(g.values),
                "declarations": [
                    {"name": d.name, "file": d.file, "line": d.line, "lang": d.lang}
                    for d in g.decls
                ],
            }
        print(json.dumps({
            "scanned_py_files": len(py_files),
            "scanned_ts_files": len(ts_files),
            "duplicate_groups": [_g(g) for g in groups],
            "gating_groups": [_g(g) for g in gating],
        }, indent=2, sort_keys=False))
        return 1 if gating else 0

    print(f"check_one_definition: {len(py_files)} python file(s), "
          f"{len(ts_files)} typescript file(s) scanned")
    if not groups:
        print("check_one_definition: clean -- no value-set declared in two places")
        return 0

    print(f"\n=== {len(groups)} enum value-set(s) declared in more than one place")
    for g in groups:
        mark = " <- GATING (touches the current diff)" if g in gating else ""
        print(f"    {{{', '.join(sorted(g.values))}}}{mark}")
        for d in g.decls:
            print(f"        {d.name:24} {d.file}:{d.line}  ({d.lang})")

    if args.only_changed is not None:
        if gating:
            print(f"\ncheck_one_definition: {len(gating)} of those group(s) touch a "
                  f"file changed since {args.only_changed} -- consolidate to one "
                  f"definition, or import the canonical one, before this lands.")
        else:
            print(f"\ncheck_one_definition: none of the {len(groups)} pre-existing "
                  f"duplicate group(s) touch a file changed since {args.only_changed}; "
                  f"not blocking this diff for debt it didn't introduce.")

    return 1 if gating else 0


if __name__ == "__main__":
    raise SystemExit(main())
