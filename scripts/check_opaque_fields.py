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


def scan() -> dict:
    doc, error = _generate_schema()
    if doc is None:
        return {"ok": False, "reason": "generator_failed", "error": error}

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

    regressions = _regressions(current, baseline) if baseline else []

    if args.json:
        print(json.dumps({**current, "regressions": [
            {"schema": s, "baseline": b, "current": c} for s, b, c in regressions]},
            indent=2, sort_keys=True))
        return 1 if regressions else 0

    print(f"check_opaque_fields: {current['schemas_scanned']} schema(s) scanned")
    print(f"    opaque field(s):      {current['opaque_fields']}")
    print(f"    schema(s) affected:   {current['schemas_with_opaque_fields']}")

    if args.list:
        for name, fields in sorted(current["per_schema"].items()):
            print(f"    {name}: {', '.join(fields)}")

    if not baseline:
        print(f"\ncheck_opaque_fields: no baseline at {baseline_path}. "
              f"Run with --write-baseline to pin one.")
        return 0

    if not regressions:
        print("\ncheck_opaque_fields: no regression against the pinned baseline")
        return 0

    print(f"\n=== {len(regressions)} schema(s) gained an untyped field")
    for name, was, now in regressions:
        gained = sorted(set(current["per_schema"][name])
                        - set(baseline.get("per_schema", {}).get(name, [])))
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
