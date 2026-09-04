#!/usr/bin/env python3
"""The assembled chat prefix has a budget, and it is checked at review time.

WHY A GATE AND NOT A RUNTIME LIMIT. The prompt has three places it can respond
to being too big, and they answer differently on purpose:

  - BLOCK LEVEL, at runtime: trim, visibly, under the block's own declared cap,
    naming what was dropped. Only the block's own author may do this.
  - ASSEMBLY LEVEL, at runtime: log, never fail. A fuse that fires on the
    operator mid-sentence is a fuse that gets disabled, and the budget goes with
    it. `routes/chat._warn_if_over_prefix_budget` is that half.
  - REVIEW LEVEL: FAIL. Review is the only point at which an over-budget prefix
    can actually be FIXED, so it is the only point where blocking helps rather
    than destroys. That is this script.

WHY IT MEASURES STATICALLY, WHICH IS A DELIBERATE DIVERGENCE FROM THE THING IT
PROTECTS. The assembled size depends on instance-local store CONTENT -- the
runtime directory, a project brief, the knowledge store -- none of which is
tracked. A gate whose number moves when the operator writes a note is a gate
that gets re-pinned until it means nothing, which is exactly how the previous
generation of gates in this repo died. So this measures what the CODE commits
to, and nothing else:

  static_chars           module-level prompt-text constants over 200 chars, by
                         AST walk, in an enumerated file set. What the platform
                         says on every turn regardless of any store.
  declared_budget_chars  the sum of the caps in chat_context.PROMPT_BLOCKS. A
                         FLOOR on the declared total, not the total, because
                         uncapped rows contribute nothing to it.
  uncapped_blocks        rows in PROMPT_BLOCKS with no byte budget. THIS is the
                         count that would have caught the block that read a
                         growing store whole on the day it was written, and it
                         is the reason the registry exists rather than a comment.

Gate conditions, three ratcheting down plus one absolute:

    static_chars                              <= baseline
    declared_budget_chars                     <= baseline
    uncapped_blocks                           <= baseline        (target 0)
    static_chars + declared_budget_chars      <= ceiling_chars   (see below)

THE CEILING IS NOT ENFORCED WHILE ANY BLOCK IS UNCAPPED, and that is not
leniency. An uncapped block contributes 0 to the sum, so a ceiling checked
against an incomplete sum reads as PASSING while the thing it is supposed to
bound is unbounded -- the precise failure this whole exercise is about.
`uncapped_blocks` reaching 0 is what arms it. Bringing that count down is
therefore the work, not re-pinning the other two.

A NOTE ON `declared_budget_chars` GOING DOWN. Lower is better only when it is
lower because a cap tightened. It also goes down when a capped block is DELETED
and, perversely, when a capped block becomes uncapped -- which is why
`uncapped_blocks` is checked as its own count rather than folded in. Read the
two together; either one alone can be gamed.

DELTA MODE IS NOT OFFERED, AND THAT IS ON PURPOSE. The `--since` pattern exists
so a change is not blamed for inherited debt, and it works by scanning a
`git archive` extract. All three counts here are pure functions of tracked
source, so a regression is always attributable to the diff under review and
there is no inherited-debt problem to solve. Adding `--since` would be ceremony.
Do not add it for symmetry with the other gates.

PIN FROM A COMMITTED TREE. `--write-baseline` mid-edit reads the working tree
and then fails the next run for no reason; the same warning is on
check_no_app_names.py for the same measured reason. Commit, then pin.

    python3 scripts/check_prompt_size.py
    python3 scripts/check_prompt_size.py --json
    python3 scripts/check_prompt_size.py --measure-live      # advisory, ungated
    python3 scripts/check_prompt_size.py --write-baseline --because '<why>'
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "prompt_size_baseline.json"

# The files whose module-level string constants ARE the platform's prompt text.
# Enumerated rather than discovered: a scan of every file for long strings would
# count SQL, HTML and test fixtures, and the number would stop meaning "what the
# platform says to a model". Adding a prompt-text module means adding it here.
PROMPT_TEXT_FILES = (
    "backend/services/chat_context.py",
    "backend/services/orientation.py",
    "backend/services/custom_instructions.py",
    "backend/services/app_persona.py",
    "backend/routes/chat.py",
)

# Below this, a module-level string is a label, a key or a short format string
# rather than prompt text. 200 is the threshold the design named; it is not
# load-bearing, but changing it moves every count and so needs a re-pin.
MIN_PROMPT_STRING = 200

COUNTS = ("static_chars", "declared_budget_chars", "uncapped_blocks")


def _static_strings(path: Path) -> list[tuple[str, int]]:
    """(name, chars) for every module-level string constant over the threshold.

    MODULE LEVEL ONLY, by AST. A docstring inside a function is documentation
    for a reader; a module-level constant is text that ships to a model. Walking
    the AST rather than grepping is what keeps a long comment or a test fixture
    out of the number.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    out: list[tuple[str, int]] = []
    for node in tree.body:
        targets, value = _assigned(node)
        if value is None or not targets:
            continue
        text = _const_str(value)
        if text is None or len(text) < MIN_PROMPT_STRING:
            continue
        out.append((targets[0], len(text)))
    return out


def _const_str(node: ast.AST) -> str | None:
    """The string a module-level expression evaluates to, or None.

    Handles the two shapes prompt text is actually written in here: a plain
    literal (including implicit concatenation, which the parser has already
    joined) and an explicit `"a" + "b"` chain across lines. Anything with a
    name, a call or an f-string in it is NOT counted, because its size is not a
    property of this file alone -- and a number that silently depends on
    something else is the kind that drifts.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _const_str(node.left)
        right = _const_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def measure() -> dict:
    """The three counts, plus the detail behind each so a failure is actionable."""
    per_file: dict[str, int] = {}
    biggest: list[tuple[str, str, int]] = []
    for rel in PROMPT_TEXT_FILES:
        p = ROOT / rel
        if not p.exists():
            per_file[rel] = 0
            continue
        strings = _static_strings(p)
        per_file[rel] = sum(n for _, n in strings)
        biggest.extend((rel, name, n) for name, n in strings)
    biggest.sort(key=lambda r: -r[2])

    budget = _registry_budget()
    return {
        "static_chars": sum(per_file.values()),
        "declared_budget_chars": budget["capped_chars"],
        "uncapped_blocks": len(budget["uncapped"]),
        "_static_by_file": per_file,
        "_largest_constants": [
            {"file": f, "name": n, "chars": c} for f, n, c in biggest[:10]],
        "_uncapped": budget["uncapped"],
        "_capped_by_block": budget["by_block"],
        "_blocks": budget["blocks"],
    }


class RegistryUnreadable(Exception):
    """The registry could not be read, which is a FAILURE and never a pass.

    An unreadable registry yields no rows, so every derived count reads as 0 --
    including `uncapped_blocks`, which would ARM the ceiling against a sum of
    nothing and print a green line. That is the same shape as the defect this
    whole gate exists to catch, so it raises instead."""


def _registry_budget() -> dict:
    """The declared budget, read from SOURCE by AST. Nothing is imported.

    The first version of this imported `chat_context` and called
    `prefix_budget()`. It failed immediately, and instructively: the reviewer
    runs gates under the system interpreter, which has none of the backend's
    dependencies, so the import raised, the fallback returned an empty registry,
    and the run printed `uncapped_blocks: 0` and armed the ceiling against a sum
    of zero. A gate that reports PASS when it cannot see anything is worse than
    no gate -- it is the exact failure the header attributes to the predecessor
    it replaced, reproduced inside the replacement on its first run.

    Reading the source has three properties the import did not: it needs no
    virtualenv, it works on a fresh clone, and it is what the ticket's own
    boundary asks for ("measure statically from tracked source only"). The cost
    is that the dotted cap paths have to be resolved by parsing the module they
    name, which is what `_resolve_static` does.
    """
    src = ROOT / "backend" / "services" / "chat_context.py"
    try:
        tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        raise RegistryUnreadable(f"cannot parse {src}: {exc}") from exc

    rows = None
    excluded: tuple[str, ...] = ()
    for node in tree.body:
        # BOTH assignment forms. `PROMPT_BLOCKS: tuple[...] = (...)` is an
        # AnnAssign, and reading only Assign is how the first run of this
        # reported the registry missing when it was right there.
        names, value = _assigned(node)
        if "PROMPT_BLOCKS" in names:
            rows = value
        elif "PREFIX_EXCLUDED_BLOCKS" in names and value is not None:
            try:
                excluded = tuple(ast.literal_eval(value))
            except (ValueError, SyntaxError):
                excluded = ()
    if rows is None or not isinstance(rows, (ast.Tuple, ast.List)):
        raise RegistryUnreadable(
            "chat_context.PROMPT_BLOCKS is missing or is not a literal "
            "tuple/list. It must stay a literal: a registry built at runtime "
            "cannot be summed without running the assembler, which is the one "
            "thing this gate may not do.")

    capped: dict[str, int] = {}
    uncapped: list[str] = []
    total_rows = 0
    for call in rows.elts:
        row = _row_from_call(call)
        if row is None:
            raise RegistryUnreadable(
                "a PROMPT_BLOCKS entry is not a plain PromptBlock(...) literal")
        name, cap, unit = row
        if name in excluded:
            continue
        total_rows += 1
        n = _cap_chars(cap, unit)
        if n is None:
            uncapped.append(name)
        else:
            capped[name] = n
    return {"capped_chars": sum(capped.values()), "by_block": capped,
            "uncapped": uncapped, "blocks": total_rows}


# Positional signature of PromptBlock(name, kind, source, cap, cap_unit, ...).
_ROW_NAME, _ROW_CAP, _ROW_UNIT = 0, 3, 4
# The four cap-unit tokens, resolved from their module-level constants so this
# script and chat_context cannot disagree about their spelling.
_UNIT_NAMES = {"CAP_STATIC": "static", "CAP_CHARS": "chars",
               "CAP_TOKENS": "tokens", "CAP_COUNT": "count"}
CHARS_PER_TOKEN = 4


def _assigned(node: ast.AST) -> tuple[list[str], ast.AST | None]:
    """(names, value) for a module-level assignment, either form."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id], node.value
    return [], None


def _row_from_call(node: ast.AST) -> tuple[str, str | None, str] | None:
    """(name, dotted cap path or None, cap unit) from one registry entry."""
    if not isinstance(node, ast.Call):
        return None
    args = node.args
    if len(args) <= _ROW_UNIT:
        return None
    name = _const_str(args[_ROW_NAME])
    cap = _const_str(args[_ROW_CAP])
    unit_node = args[_ROW_UNIT]
    if isinstance(unit_node, ast.Name):
        unit = _UNIT_NAMES.get(unit_node.id)
    else:
        unit = _const_str(unit_node)
    if name is None or unit is None:
        return None
    return name, cap, unit


def _cap_chars(cap: str | None, unit: str) -> int | None:
    """A row's cap in chars, resolved from source, or None if it has none."""
    if cap is None or unit == "count":
        # A COUNT IS NOT A BYTE BUDGET. Six tickets is six titles or six
        # essays, and R3 needs a number it can sum.
        return None
    value = _resolve_static(cap)
    if value is None:
        return None
    if unit == "static":
        return len(value) if isinstance(value, str) else None
    if not isinstance(value, int):
        return None
    return value * CHARS_PER_TOKEN if unit == "tokens" else value


def _resolve_static(dotted: str) -> object | None:
    """`services.knowledge.WORKING_STATE_MAX_CHARS` -> 4800, by parsing.

    Returns None when the constant cannot be resolved, which the caller reports
    as uncapped. That direction is deliberate: an unresolvable cap makes the
    count go UP and the ratchet fail, so a renamed constant is loud. Resolving
    it to some default would make it quiet, and a cap that silently stops being
    read is how the registry and the code drift apart.
    """
    mod_path, _, attr = dotted.rpartition(".")
    if not mod_path or not attr:
        return None
    rel = Path("backend") / Path(mod_path.replace(".", "/") + ".py")
    src = ROOT / rel
    if not src.exists():
        return None
    try:
        tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if attr not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        text = _const_str(node.value)
        if text is not None:
            return text
        return _const_int(node.value)
    return None


def _const_int(node: ast.AST) -> int | None:
    """An int constant, including the repo's env-override idiom.

    `int(os.environ.get("X", "1800"))` is how three of these caps are written,
    so the DEFAULT is the declared budget and the env var is an operator's
    local override. The gate pins the declared default: a number that changed
    with the shell would not be a ratchet.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "int" and node.args:
        inner = node.args[0]
        if isinstance(inner, ast.Call) and inner.args:
            # os.environ.get("NAME", "default") -> the default
            default = inner.args[-1]
            text = _const_str(default)
            if text is not None:
                try:
                    return int(text)
                except ValueError:
                    return None
        return _const_int(inner)
    return None


def measure_live() -> int:
    """ADVISORY, UNGATED: the real per-block breakdown of a live prefix.

    Never part of the gate, for the store-content reason in the header. This is
    how a person answers "what is it actually right now", which is a different
    and equally real question from "what has the code committed to".
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from services import chat_context
    rc = 0
    for key in ("general", "development"):
        try:
            provider = chat_context.get_provider(key)
            parts = [
                chat_context.platform_kernel(),
                chat_context.role_preamble(tools_active=True),
                chat_context.runtime_briefing(provider),
                chat_context.orientation_manifest(provider),
                provider.persona(),
                chat_context.learnings_context(provider),
                chat_context.ambient_digest(provider),
                chat_context.working_state_block(provider),
                provider.custom_prompt(),
                provider.live_context(),
            ]
        except Exception as exc:
            print(f"{key}: could not assemble ({exc})")
            rc = 1
            continue
        total = chat_context.prefix_chars(parts)
        print(f"\n{key}: {total} chars (~{total // 4} tokens), "
              f"budget {chat_context.PREFIX_WARN_CHARS}")
        for name, n in chat_context.prefix_breakdown(parts):
            print(f"    {n:>8}  {name}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-live", action="store_true",
                    help="ADVISORY: assemble a real prefix per scope and print "
                         "the per-block breakdown. Never gated.")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--because", default="",
                    help="Required when a pin RAISES a count. Recorded in the "
                         "baseline and printed on every run afterwards, so an "
                         "upward pin cannot be quiet.")
    args = ap.parse_args()

    if args.measure_live:
        return measure_live()

    try:
        result = measure()
    except RegistryUnreadable as exc:
        # NEVER a pass. See RegistryUnreadable.
        print(f"check_prompt_size: FAIL, the registry could not be read: {exc}",
              file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    ceiling = int(base.get("ceiling_chars") or 0)

    if args.write_baseline:
        raised = {k: (base.get(k), result[k]) for k in COUNTS
                  if base.get(k) is not None and result[k] > base[k]}
        if raised and not args.because:
            for k, (was, now) in sorted(raised.items()):
                print(f"pin would RAISE {k}: {was} -> {now}")
            print("\nRefusing. Re-pin an increase with --because '<why>'.")
            return 1
        payload = {
            "_comment": (
                "Ratchet for check_prompt_size.py. These counts only go DOWN. "
                "ceiling_chars is enforced only once uncapped_blocks reaches 0, "
                "because a sum over an incomplete registry is not a sum: an "
                "uncapped block contributes 0, so the ceiling would read as "
                "passing while the thing it bounds is unbounded. Driving "
                "uncapped_blocks to 0 is the work; re-pinning the other two is "
                "not."
            ),
            "ceiling_chars": ceiling or 46000,
            **{k: result[k] for k in COUNTS},
        }
        if args.because:
            payload["because"] = args.because
        elif base.get("because"):
            payload["because"] = base["because"]
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"pinned {BASELINE.relative_to(ROOT)}: "
              + ", ".join(f"{k}={result[k]}" for k in COUNTS))
        return 0

    # --json PRINTS JSON AND NOTHING ELSE. It used to print the payload and then
    # the human report underneath it, so every machine reader had to find the
    # closing brace by hand; the first caller to try `json.loads` on it failed
    # on "Extra data". A mode called --json that does not emit parseable output
    # is not a mode, it is a trap.
    say = (lambda *a, **k: None) if args.json else print

    failed = False
    if not base:
        say("check_prompt_size: no baseline; run --write-baseline to pin one")
        for k in COUNTS:
            say(f"    {k}: {result[k]}")
        if args.json:
            print(json.dumps({**result, "baseline": base}, indent=2))
        return 0

    if base.get("because"):
        say(f"check_prompt_size: pinned upward, reason on record: {base['because']}")

    for k in COUNTS:
        pinned = base.get(k)
        if pinned is None:
            say(f"  {k}: {result[k]} (unpinned)")
            continue
        if result[k] > pinned:
            say(f"  FAIL {k}: {result[k]} > pinned {pinned}")
            failed = True
        elif result[k] < pinned:
            say(f"  improved {k}: {pinned} -> {result[k]}")
        else:
            say(f"  ok {k}: {result[k]}")

    total = result["static_chars"] + result["declared_budget_chars"]
    ceiling_enforced = result["uncapped_blocks"] == 0
    if ceiling_enforced:
        if ceiling and total > ceiling:
            say(f"  FAIL ceiling: {total} > {ceiling}")
            failed = True
        else:
            say(f"  ok ceiling: {total} <= {ceiling}")
    else:
        say(f"  ceiling NOT ENFORCED: {result['uncapped_blocks']} block(s) "
            f"declare no byte budget, so the {total}-char sum is incomplete "
            f"and comparing it to {ceiling} would measure nothing. Uncapped: "
            + ", ".join(result["_uncapped"]))

    if args.json:
        print(json.dumps({**result, "baseline": base, "total_chars": total,
                          "ceiling_enforced": ceiling_enforced,
                          "failed": failed}, indent=2))
        return 1 if failed else 0

    if failed:
        print("\ncheck_prompt_size: the prompt budget got worse.")
        for row in result["_largest_constants"][:5]:
            print(f"    {row['chars']:>7}  {row['file']}:{row['name']}")
        return 1
    print("check_prompt_size: the prompt budget did not get worse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
