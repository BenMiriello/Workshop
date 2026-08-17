"""Latitude — what a worker is authorized to do when the ticket does not say.

One field, one cascade. Every ticket underspecifies something; that is what a
ticket is. How MUCH a ticket underspecifies ("scopedness") is an observation, not
a dial — a `scopedness: high` field on a vague ticket would be a lie stored in
JSON. What IS settable is the standing policy for the underspecified residue:

    strict    Do what the ticket says. Any decision the ticket does not specify
              raises a block. Many blocks. Use for an unfamiliar area, or a
              ticket written carefully and meant literally.
    standard  Decide REVERSIBLE things and record the decision as evidence.
              Block on irreversible ones, on anything outside `touches`, and on
              any hard gate. Few blocks. The default.
    open      Pursue the goal, decide freely, report decisions at the end.
              Rare blocks.

Latitude IS the block rate: the escalation loop is not a subsystem beside this
dial, it is the dial's runtime behavior.

WHAT THIS IS: PROMPT TEXT. Say it plainly, because this docstring used to say
something else. Latitude's effect is that the resolved value is written into the
sweep's instructions (`ralph.sh`, LATITUDE_RULE_*) and the model is asked to
behave accordingly. It has exactly ONE machine-visible consequence anywhere:
`project_ralph.sh` treats ralph's rc=7 — a `strict` run whose completion gate
could not establish completeness — as a block for the operator rather than a
retry. Everything else about latitude is persuasion, and a model that ignores it
is not stopped by anything.

It is therefore NOT a permission, and nothing enforced sits beneath it for it to
be "above". That phrasing was here, and it was wrong twice over: `touches`
(`tasks.enforce_touches`) is an AFTER-THE-FACT AUDIT — it runs on the committed
diff once the run is over, so the write it names already happened and is already
committed — and it flags strays by blocking the ticket, not by refusing the
write. Resource grants gate the mediated Store paths and nothing the agent's own
tools do. None of the three may be described in the UI or in a prompt as a
boundary that stops a run mid-write, because none of them is one.

Both mechanisms are worth keeping and neither is being weakened here. An audit
that reliably catches a stray and hands it to the operator is a real, useful
thing. It is just not a wall, and calling it one is how the operator ends up
trusting a wall that is not there.

The cascade (identical to `depth`; no new mechanism):

    invocation  >  task  >  scope  >  DEFAULT_LATITUDE

First non-null wins. **Null means inherit, it does not mean "standard".**
DEFAULT_LATITUDE applies only when the whole chain is null; otherwise a project
set to `strict` would be silently undone by a task record that stores its own
default. This is why every writer emits latitude only when it is non-default
(the same discipline agents_registry applies to depth/fanout).

Scope resolution follows the TREE, not the worker's personality (report 4.3): an
agent scope and a project scope are peers, and the scope level of the cascade is
whichever scope owns the tree the task lives in. An agent with `latitude: open`
in its frontmatter does not import its own permissiveness into a project the
operator marked `strict`; its own value applies in its own scope. A per-task
override covers the exception.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from wsplatform import paths

LATITUDES = ("strict", "standard", "open")
DEFAULT_LATITUDE = "standard"

# Where the resolved value came from, for the UI. An inherited value that looks
# set is how the null-means-inherit bug becomes invisible, so the source travels
# with the value everywhere it is surfaced.
SOURCES = ("invocation", "task", "scope", "default")


def coerce(v: object) -> str | None:
    """Read-side: a canonical latitude, or None meaning INHERIT.

    Junk, a typo, or an unset field all read as None (inherit), never as
    `standard` — coercing a typo to the default would silently undo a project's
    setting. Writes go through `validate`, which raises instead of coercing, so
    a bad value can only enter a store by hand-editing a file."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    return s if s in LATITUDES else None


def validate(v: object) -> str | None:
    """Write-side: a canonical latitude, or None for unset/empty (= inherit).
    Raises ValueError on an unknown value so an API write surfaces a 400 rather
    than persisting a field nothing will honor."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"latitude must be a string, one of {LATITUDES}")
    s = v.strip().lower()
    if not s:
        return None
    if s not in LATITUDES:
        raise ValueError(f"unknown latitude {v!r}; must be one of {LATITUDES}")
    return s


def scope_latitude(scope_key: str | None) -> str | None:
    """The latitude set on the scope that owns a tree, or None (inherit).

    `project:<slug>` reads the project registry's autonomy record; `agent:<slug>`
    reads the agent definition's frontmatter. Every other scope (the workshop
    root, a build tree) has no scope-level setting and inherits the default.
    Imports are deferred: agents_registry imports this module for its enum, so a
    module-level import here would be circular."""
    if not scope_key or ":" not in scope_key:
        return None
    kind, _, slug = scope_key.partition(":")
    if not slug:
        return None
    if kind == "project":
        registry = paths.env_path("WORKSHOP_PROJECTS_REGISTRY", "projects.json")
        try:
            reg = json.loads(registry.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        proj = (reg.get("projects") or {}).get(slug) or {}
        return coerce((proj.get("autonomy") or {}).get("latitude"))
    if kind == "agent":
        from services import agents_registry  # deferred: circular at module level
        rec = agents_registry.get(slug)
        return coerce(rec.get("latitude")) if rec else None
    return None


def resolve(*, invocation: object = None, task: dict | None = None,
            scope_key: str | None = None) -> tuple[str, str]:
    """The effective latitude and WHERE it came from: (value, source).

    invocation > task > scope > DEFAULT_LATITUDE, first non-null wins."""
    inv = coerce(invocation)
    if inv:
        return inv, "invocation"
    t = coerce((task or {}).get("latitude"))
    if t:
        return t, "task"
    s = scope_latitude(scope_key)
    if s:
        return s, "scope"
    return DEFAULT_LATITUDE, "default"


def resolve_for_task(root: Path, task_id: str, *, scope_key: str | None = None,
                     invocation: object = None) -> tuple[str, str]:
    """`resolve` for a task read from a store on disk."""
    from services import tasks  # deferred: tasks does not need latitude at import
    t = tasks.get_task(root, task_id)
    return resolve(invocation=invocation, task=t, scope_key=scope_key)


def main(argv: list[str]) -> int:
    """`python -m services.latitude resolve ...` — the shell's entry point.

    ralph.sh resolves the effective latitude at claim time through this, so the
    sweep and the API can never disagree about the cascade."""
    ap = argparse.ArgumentParser(prog="latitude")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("resolve")
    sp.add_argument("--scope", default=None, help="scope key that owns the tree")
    sp.add_argument("--store-dir", type=Path, default=None)
    sp.add_argument("--task-id", default=None)
    sp.add_argument("--invocation", default=None, help="per-run override")
    sp.add_argument("--field", default=None, choices=("value", "source"),
                    help="print one field instead of JSON")
    args = ap.parse_args(argv)

    if args.store_dir and args.task_id:
        value, source = resolve_for_task(
            args.store_dir, args.task_id, scope_key=args.scope,
            invocation=args.invocation)
    else:
        value, source = resolve(invocation=args.invocation, task=None,
                                scope_key=args.scope)
    if args.field == "value":
        print(value)
    elif args.field == "source":
        print(source)
    else:
        print(json.dumps({"latitude": value, "source": source}))
    return 0


if __name__ == "__main__":
    _BACKEND = str(Path(__file__).resolve().parent.parent)
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    raise SystemExit(main(sys.argv[1:]))
