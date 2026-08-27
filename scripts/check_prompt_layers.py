#!/usr/bin/env python3
"""No fact about ONE installation in PLATFORM prompt text.

THE RULE (CLAUDE.md, from docs/platform-redesign/
agent-context-and-toolkit-2026-08-20.md "The four-layer contract"). L0 (kernel),
L1 (orientation) and L2 (persona) are PLATFORM text: every installation and
every user receives them, so they may carry only generalized product doctrine.
They may not carry a fact about one checkout -- a doc path, a repo convention, a
named person, an operator's habits -- and may not assume the reader's pronouns.
L3 (services/custom_instructions.py) is the operator's own and is exempt, and so
are a repo's own AGENTS.md / CLAUDE.md.

CLAUDE.md said of that rule "it has no gate yet, so it is on you". It had not
been on anyone: on 2026-08-27 there were eleven violations, including "live for
him until the bundle is rebuilt", `~/workshop/scripts/project_tasks.py` in two
personas, and a nine-name egress allowlist. This is the gate.

WHAT IT SCANS. The surfaces named in prompt_layer_rules.json, and inside them
only PROSE STRING LITERALS: a Python `str` constant, excluding docstrings
(comments are never parsed, so nothing here fires on a code comment explaining
the rule) and excluding literals with no whitespace. That last exclusion is what
separates prompt text from code data: `_RULES_FILES = ("AGENTS.md", ...)` is a
list of filenames the platform READS, not text a model receives. A file whose
prompt text is a minority of the module declares `symbols`, and only those
top-level constants/functions are scanned.

WHAT COUNTS. The four detectors in the declaration: gendered third-person
pronouns, `~/` and host absolute paths, `.md` filenames, and the vendor list.
Every one of them, and the surface list, lives in prompt_layer_rules.json (the
floor lives in prompt_layers_baseline.json, see below) -- this script holds no
fact about the tree, the same shape backend/services/app_persona.py uses for app
assistants.

THE FLOOR IS ZERO and this fails above it. The floor lives in its own file,
prompt_layers_baseline.json, rather than in the declaration beside it, because
check_ratchets_not_loosened.py globs `*baseline*.json` and must be able to see an
entry being added to `allowed` -- while the declaration's own lists (surfaces,
vendors) GROW as the rule is applied more widely, which that gate would read
backwards. `allowed` is empty as of 2026-08-27 because the eleven were fixed in
the commit that added this, so there is nothing inherited to forgive. A gate that
reports a violation and exits 0 is the exact failure mode this exists to fix.

Usage:
    python3 scripts/check_prompt_layers.py            # exit 1 on any offense
    python3 scripts/check_prompt_layers.py --json
    python3 scripts/check_prompt_layers.py --since REV # fail only on a NEW one
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

_WORKSHOP = Path(__file__).resolve().parents[1]
_RULES_PATH = Path(__file__).resolve().parent / "prompt_layer_rules.json"
_BASELINE_PATH = Path(__file__).resolve().parent / "prompt_layers_baseline.json"


def _rules(path: Path = _RULES_PATH) -> dict:
    return json.loads(path.read_text())


def _floor(path: Path = _BASELINE_PATH) -> list[str]:
    """The pinned allowlist: offenses this tree tolerates today, and nothing
    else. Empty means the gate is absolute."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    allowed = data.get("allowed") if isinstance(data, dict) else None
    return sorted(allowed) if isinstance(allowed, list) else []


def _detectors(rules: dict) -> list[tuple[str, re.Pattern[str], str]]:
    """(name, compiled, fix) per declared pattern. The vendor detector is built
    from the `vendors` list so adding a vendor is a data edit, never a code one."""
    out = []
    for name, spec in rules["patterns"].items():
        if spec.get("from_vendors"):
            src = r"\b(" + "|".join(re.escape(v) for v in rules["vendors"]) + r")\b"
        else:
            src = spec["regex"]
        out.append((name, re.compile(src, re.I if spec.get("ignorecase") else 0),
                    spec["fix"]))
    return out


def _prose_literals(src: str, symbols: list[str] | None):
    """Yield (lineno, text) for every prose string literal in `src`.

    Docstrings are skipped (they document the code, they are not sent to a
    model) and so are literals with no whitespace (a bare filename or key is
    code data). `symbols` restricts the walk to named top-level constants and
    functions."""
    tree = ast.parse(src)
    roots: list[ast.AST] = [tree]
    if symbols:
        roots = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in symbols:
                roots.append(node)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in symbols for t in node.targets):
                roots.append(node)
    skip: set[int] = set()
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                if ast.get_docstring(node, clean=False) is not None and node.body \
                        and isinstance(node.body[0], ast.Expr):
                    skip.add(id(node.body[0].value))
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in skip and any(c.isspace() for c in node.value):
                yield node.lineno, node.value


def _offenses(root: Path = _WORKSHOP, rules: dict | None = None) -> list[str]:
    """`<relfile> :: <detector> :: <match>` for every offense under `root`.

    Line numbers are NOT part of the identity -- the file, the detector and the
    matched text are -- so an unrelated edit that shifts lines never churns the
    result.
    """
    rules = rules or _rules()
    dets = _detectors(rules)
    found: set[str] = set()
    for surface in rules["surfaces"]:
        path = root / surface["path"]
        if not path.is_file():
            continue
        try:
            literals = list(_prose_literals(path.read_text(encoding="utf-8"),
                                            surface.get("symbols")))
        except (OSError, SyntaxError):
            continue
        for _, text in literals:
            for name, pat, _fix in dets:
                for m in pat.finditer(text):
                    found.add(f"{surface['path']} :: {name} :: {m.group(0)}")
    return sorted(found)


def _locate(rel: str, detector: str, match: str, rules: dict) -> str:
    """First line the offense appears on, for the human-readable report only."""
    surface = next(s for s in rules["surfaces"] if s["path"] == rel)
    pat = dict((n, p) for n, p, _ in _detectors(rules))[detector]
    for lineno, text in _prose_literals((_WORKSHOP / rel).read_text(), surface.get("symbols")):
        for m in pat.finditer(text):
            if m.group(0) == match:
                return f"{rel}:{lineno}"
    return rel


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rules", default=str(_RULES_PATH))
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: fail only on an offense that is new since REV.")
    args = ap.parse_args()

    rules = _rules(Path(args.rules))
    allowed = _floor(Path(args.baseline))
    current = _offenses(rules=rules)

    since: list[str] | None = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, _WORKSHOP) as base:
                since = _offenses(base, rules)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_prompt_layers: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    # Delta mode still honours the pinned floor: an offense the base ref also had
    # is inherited, but one on the `allowed` list is forgiven either way.
    inherited = set(since or []) | set(allowed)
    offenders = [o for o in current if o not in inherited]

    if args.json:
        print(json.dumps({"current": current, "allowed": allowed,
                          "since_ref": args.since, "since": since,
                          "offenders": offenders}, indent=2))
        return 1 if offenders else 0

    if offenders:
        print(f"check_prompt_layers: {len(offenders)} installation-specific "
              f"fact(s) in PLATFORM prompt text:")
        fixes = {n: f for n, _, f in _detectors(rules)}
        for o in offenders:
            rel, detector, match = o.split(" :: ", 2)
            print(f"    {_locate(rel, detector, match, rules)}  [{detector}] {match!r}")
            print(f"        {fixes[detector]}")
        print("\nL0/L1/L2 ship to every installation and every user. A fact about "
              "one checkout belongs in that repo's AGENTS.md / CLAUDE.md, in the "
              "operator's L3 custom instructions, or in the installation "
              "declaration (backend/services/installation.py).")
        return 1

    scanned = ", ".join(s["path"] for s in rules["surfaces"])
    print(f"check_prompt_layers: platform prompt text is clean ({len(current)} "
          f"offense(s), floor {len(allowed)}) across: {scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
