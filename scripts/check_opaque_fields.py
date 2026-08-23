#!/usr/bin/env python3
"""Response FIELDS that are typed as nothing, ratcheted. The second half of
ADR 0023 clause 6 -- `check_response_model.py` is the first half, and on its
own it is not enough.

THE FINDING THIS PROTECTS. `check_response_model.py` counts ROUTES: it asks
whether a route names a model, and it rejects `response_model=dict` because
that satisfies the keyword while generating `unknown` in the TypeScript client.
Measured 2026-08-18, after 268 of 411 routes had been given a real model, the
same defect was alive one level down: a route can declare a perfectly real
Pydantic model whose FIELDS are `dict[str, Any]`, and the generated client
turns those into `Record<string, never>` -- a type whose every property is
`never`, so it can express nothing at all and the frontend hand-writes the
shape anyway. 98 field slots across the schema were in that state.

That is not a smaller version of the route problem, it is the same problem: the
client still cannot know what it is being sent, so it still guesses, and the
guess still drifts. The chat thread document was the worst case -- `card_states`,
`turn_marks` and `ticket_drafts` were all opaque, and the frontend's own
hand-written `ThreadSummary` had drifted in BOTH directions against the routes
that serve it. Typing those three took reading the writers and measuring 37 live
threads; nothing in the repo could have told you they needed it.

WHAT COUNTS AS OPAQUE. Derived from the schema rather than asserted, by taking
every property of every component schema, unwrapping `anyOf` null-unions, and
keeping the shapes that openapi-typescript renders as `Record<string, never>`
or `unknown`:

    {"type": "object"}                            -- `dict` / `dict[str, Any]`
    {"type": "array", "items": {"type":"object"}} -- `list[dict]`
    {}                                            -- a bare `Any`

A `dict` with a typed value is NOT opaque: `{"additionalProperties": {"$ref":
...}}` and `{"additionalProperties": {"type": "string"}}` both generate a usable
type, which is the whole point of writing `dict[str, StoredCardState]` instead
of `dict[str, Any]`. Neither is a model that declares its fields AND allows
extras (`ConfigDict(extra="allow")`): that generates a typed floor plus an index
signature, which is the right answer whenever a payload has a known core and an
open tail, and it cannot drop a field the way a closed model can.

DELIBERATELY OPAQUE PAYLOADS EXIST AND ARE THE REASON THIS IS A RATCHET.
`ThreadEvents.events` and the live-message view stay open ON PURPOSE, argued in
`routes/chat_models.py`'s own module docstring: the event log is heterogeneous
by design and the renderer splits replies on positional fields a closed model
would drop -- dropping exactly those is the 2026-08-14 regression that put the
operator's message above half an hour of work. `ContextDetail`'s CLI-derived
block is another: `/context` is parsed out of another process's markdown and
its section set belongs to that CLI, not to us. A gate on the absolute number
would demand those be closed, which would be actively wrong. A ratchet asks
only that the number not grow, which is the correct ask.

Usage:
    python3 scripts/check_opaque_fields.py
    python3 scripts/check_opaque_fields.py --json
    python3 scripts/check_opaque_fields.py --list
    python3 scripts/check_opaque_fields.py --write-baseline
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

_WORKSHOP = Path(__file__).resolve().parents[1]
_GENERATOR = _WORKSHOP / "scripts" / "gen_openapi.py"
_BASELINE_PATH = Path(__file__).resolve().parent / "opaque_fields_baseline.json"


def _generate_schema() -> tuple[dict | None, str]:
    """Regenerate the OpenAPI document from the CURRENT tree.

    Regenerated rather than read from `tmp/openapi.json` for the reason
    `check_generated_client.py` gives: a committed or cached artifact passes
    for anyone who touched it for any reason, and the point of the check is to
    say something true about THIS source tree.
    """
    tmp = Path(tempfile.mkdtemp(prefix="workshop-opaque-"))
    try:
        out = tmp / "openapi.json"
        res = subprocess.run(
            [sys.executable, str(_GENERATOR), "--schema-out", str(out),
             "--schema-only", "--quiet"],
            cwd=_WORKSHOP, capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-12:]
            return None, (f"{_GENERATOR.name} exited {res.returncode}:\n    "
                          + "\n    ".join(tail))
        return json.loads(out.read_text(encoding="utf-8")), ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"could not generate the schema: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _is_opaque(node: dict) -> bool:
    """True when openapi-typescript would render this as `Record<string,
    never>` or `unknown` -- i.e. a shape that carries no information."""
    if not isinstance(node, dict):
        return False
    meaningful = {k: v for k, v in node.items()
                  if k not in ("title", "description", "default", "examples")}
    if not meaningful:
        return True                                   # a bare `Any`
    if meaningful.get("type") == "object":
        # A dict with no declared fields and no declared value type. A model
        # with `properties` is fine, and so is `additionalProperties` pointing
        # at a real schema -- `additionalProperties: true` alongside properties
        # is the extra="allow" floor, which is typed.
        if "properties" in meaningful:
            return False
        ap = meaningful.get("additionalProperties")
        return ap is None or ap is True
    if meaningful.get("type") == "array":
        return _is_opaque(meaningful.get("items") or {})
    return False


def _opaque_properties(schema: dict) -> list[str]:
    """Names of this schema's properties whose type carries no information."""
    bad = []
    for field, prop in (schema.get("properties") or {}).items():
        variants = prop.get("anyOf") or prop.get("oneOf") or [prop]
        # `X | None` is two variants; the null one is not what is being typed.
        real = [v for v in variants
                if not (isinstance(v, dict) and v.get("type") == "null")]
        if real and all(_is_opaque(v) for v in real):
            bad.append(field)
    return bad


def _generate_schema_at(base_tree: Path) -> "tuple[dict | None, str]":
    """Run the generator from an extracted base-ref tree to get its schema.

    Uses the LIVE backend venv Python so the extracted tree (which has no .venv)
    still has all FastAPI/Pydantic dependencies. gen_openapi.py's own
    _reexec_in_backend_venv() skips the re-exec when the venv is absent from
    the extract and continues with the current interpreter, which is the one the
    live venv provides.
    """
    gen = base_tree / "scripts" / "gen_openapi.py"
    if not gen.exists():
        return None, f"gen_openapi.py not found in the base tree at {base_tree}"

    venv_python = _WORKSHOP / "backend" / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable

    tmp = Path(tempfile.mkdtemp(prefix="workshop-opaque-base-"))
    try:
        out = tmp / "openapi.json"
        res = subprocess.run(
            [python_exe, str(gen), "--schema-out", str(out),
             "--schema-only", "--quiet"],
            cwd=base_tree, capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-12:]
            return None, (f"{gen.name} (base) exited {res.returncode}:\n    "
                          + "\n    ".join(tail))
        return json.loads(out.read_text(encoding="utf-8")), ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"could not generate base schema: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_schema(doc: dict) -> dict:
    """Analyze an OpenAPI document for opaque fields."""
    schemas = (doc.get("components") or {}).get("schemas") or {}
    per_schema: dict[str, list[str]] = {}
    for name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            continue
        bad = _opaque_properties(schema)
        if bad:
            per_schema[name] = sorted(bad)

    return {
        "ok": True,
        "schemas_scanned": len(schemas),
        "schemas_with_opaque_fields": len(per_schema),
        "opaque_fields": sum(len(v) for v in per_schema.values()),
        "per_schema": per_schema,
    }


def scan() -> dict:
    doc, error = _generate_schema()
    if doc is None:
        return {"ok": False, "reason": "generator_failed", "error": error}
    return _scan_schema(doc)


def _regressions(current: dict, baseline: dict) -> list[tuple[str, int, int]]:
    """(schema, baseline_count, current_count) for every schema whose opaque
    field count went UP. A schema absent from the baseline is judged against 0,
    so a NEW model may not introduce one."""
    base = baseline.get("per_schema", {})
    out = []
    for name, fields in current["per_schema"].items():
        was = len(base.get(name, []))
        if len(fields) > was:
            out.append((name, was, len(fields)))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print every opaque field, not just the totals")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: scan REV's tree as well and fail only on a schema "
                         "whose opaque field count got worse since REV. The pinned "
                         "baseline is still reported as FYI. See this script's header.")
    args = ap.parse_args()

    current = scan()
    if not current["ok"]:
        print(f"check_opaque_fields: {current['error']}")
        return 1

    baseline_path = Path(args.baseline)
    if args.write_baseline:
        baseline_path.write_text(
            json.dumps({k: current[k] for k in
                        ("schemas_with_opaque_fields", "opaque_fields", "per_schema")},
                       indent=2, sort_keys=True) + "\n")
        print(f"check_opaque_fields: wrote baseline to {baseline_path} "
              f"({current['opaque_fields']} opaque field(s) across "
              f"{current['schemas_with_opaque_fields']} schema(s))")
        return 0

    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, ValueError):
        baseline = {}

    # DELTA MODE. `since_result` becomes the thing regressions are judged against;
    # the pinned baseline is still compared and still printed so inherited debt
    # stays visible even when it is not this run's fault.
    since_result: "dict | None" = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, _WORKSHOP) as base_tree:
                since_doc, err = _generate_schema_at(base_tree)
                if since_doc is None:
                    print(f"check_opaque_fields: base schema generation failed: {err}",
                          file=sys.stderr)
                    return 2
                since_result = _scan_schema(since_doc)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_opaque_fields: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    judge_against = since_result if since_result is not None else baseline
    against = f"the base ref {args.since}" if since_result is not None else "the pinned baseline"
    regressions = _regressions(current, judge_against) if judge_against else []

    if args.json:
        payload = {**current, "regressions": [
            {"schema": s, "baseline": b, "current": c} for s, b, c in regressions]}
        if since_result is not None:
            payload["since_ref"] = args.since
            payload["since"] = {k: since_result[k] for k in
                                 ("opaque_fields", "schemas_with_opaque_fields", "per_schema")}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if regressions else 0

    print(f"check_opaque_fields: {current['schemas_scanned']} schema(s) scanned")
    print(f"    opaque field(s):      {current['opaque_fields']}")
    print(f"    schema(s) affected:   {current['schemas_with_opaque_fields']}")

    if args.list:
        for name, fields in sorted(current["per_schema"].items()):
            print(f"    {name}: {', '.join(fields)}")

    if not judge_against:
        print(f"\ncheck_opaque_fields: no baseline at {baseline_path}. "
              f"Run with --write-baseline to pin one.")
        return 0

    if not regressions:
        print(f"\ncheck_opaque_fields: no regression against {against}")
    else:
        print(f"\n=== {len(regressions)} schema(s) gained an untyped field "
              f"(against {against})")
        for name, was, now in regressions:
            judge_per = (judge_against.get("per_schema") or {}).get(name, [])
            gained = sorted(set(current["per_schema"][name]) - set(judge_per))
            print(f"    {name}: {was} -> {now}   ({', '.join(gained)})")
        print("\n    A field typed `dict`, `dict[str, Any]`, `list[dict]` or `Any`")
        print("    generates `Record<string, never>` or `unknown` in the client, so")
        print("    the frontend has to hand-write the shape and can drift from it.")
        print("    Describe what the field actually holds. If it has a known core")
        print("    and an open tail, declare the core and add")
        print("    `model_config = ConfigDict(extra=\"allow\")`, which types the")
        print("    floor without letting anything be dropped.")
        print("\n    If the payload is genuinely heterogeneous by design, say why in")
        print("    the model's docstring and re-pin with --write-baseline.")

    # In delta mode, also report any gap between the working tree and the pinned
    # baseline -- inherited debt stays visible even when it is not this run's fault.
    if since_result is not None and baseline:
        inherited_regs = _regressions(current, baseline)
        new_regs = {r[0] for r in regressions}
        inherited_only = [r for r in inherited_regs if r[0] not in new_regs]
        if inherited_only:
            print(f"\n    FYI {len(inherited_only)} schema(s) past the PINNED baseline, "
                  "inherited and not caused by this change (not gating):")
            for name, was, now in inherited_only:
                print(f"        {name}: pinned {was} -> now {now}")

    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
