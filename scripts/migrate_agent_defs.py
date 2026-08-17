#!/usr/bin/env python3
"""Generate unified Agent definition files (Phase 2 migration).

Sources:
  * `.runtime/build_agents.json`  -> role: maker  (project scope when
    `project_slug` is set, else user scope). Body from the agent cwd's
    PROJECT.md when present.
  * The Letta agent list           -> no role (user scope), EXCLUDING
    per-channel bridge agents (authoritative via the channel-agent map) and the
    supervisor's internal plumbing (name-prefix skip list). Persona body from the
    Letta persona block; triggers from Letta autonomy metadata.

`.runtime/build_agents.json` stays in place as the maker execution-state file
(it stops being the registry of record but is NOT deleted).

DRY-RUN BY DEFAULT. Pass --apply to write files. Safe to run with autonomy
paused: it only reads Letta + build state and writes markdown definition files;
it mutates no execution state and starts no work.

    python3 scripts/migrate_agent_defs.py            # dry-run (prints plan)
    python3 scripts/migrate_agent_defs.py --apply    # write definition files
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path.home() / "workshop")))
sys.path.insert(0, str(WORKSHOP_ROOT / "backend"))

import httpx  # noqa: E402  (after sys.path insert)

from services import agents_registry as ar  # noqa: E402
from services import triggers as trig  # noqa: E402

BUILD_AGENTS_JSON = Path(os.environ.get(
    "WORKSHOP_BUILD_AGENTS_JSON", str(WORKSHOP_ROOT / ".runtime" / "build_agents.json")))
LETTA_URL = os.environ.get("LETTA_URL", "http://127.0.0.1:8283")
LETTA_PASS = os.environ.get("LETTA_SERVER_PASS", "workshop-local")

# Supervisor internal plumbing: skip Letta agents whose name starts with any of
# these. Per-channel agents are excluded separately (map-authoritative).
SKIP_NAME_PREFIXES = ("supervisor",)


def _load_build_agents() -> dict:
    try:
        return json.loads(BUILD_AGENTS_JSON.read_text()).get("build_agents") or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _letta_agents() -> list[dict]:
    with httpx.Client(base_url=LETTA_URL,
                      headers={"Authorization": f"Bearer {LETTA_PASS}"},
                      timeout=30.0) as c:
        r = c.get("/v1/agents/")
        r.raise_for_status()
        return r.json()


def _project_body(cwd: str | None) -> str:
    if not cwd:
        return ""
    p = Path(cwd) / "PROJECT.md"
    try:
        return p.read_text().strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def _write_def(path: Path, fm: dict, body: str, apply: bool) -> str:
    if path.exists():
        return f"skip (exists) {path}"
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ar._emit(fm, body))
        return f"WROTE {path}"
    return f"would write {path}"


def plan_builders(apply: bool) -> list[str]:
    out = []
    for slug, raw in _load_build_agents().items():
        if raw.get("state") == "retired":
            out.append(f"skip (retired) builder {slug}")
            continue
        project_slug = raw.get("project_slug")
        path = ar._path_for(slug, project_slug)
        fm = ar._build_fm(
            # A maker IS what a builder was: it works in a tree. There is no
            # `runtime` field any more (thread T2 of the coherence overhaul).
            role="maker",
            name=raw.get("name") or slug,
            purpose=raw.get("purpose") or "",
            capabilities=[],
            triggers_list=[],  # build agents carry no triggers today
            limits=dict(ar.DEFAULT_LIMITS),
            memory=None,
            project_slug=project_slug,
            letta_agent_id=None,
        )
        body = _project_body(raw.get("cwd"))
        out.append(_write_def(path, fm, body, apply))
    return out


def plan_assistants(apply: bool) -> list[str]:
    out = []
    try:
        agents = _letta_agents()
    except Exception as e:  # noqa: BLE001
        out.append(f"SKIP assistants: Letta unreachable ({e})")
        return out
    excluded = ar.per_channel_ids()
    taken = ar.all_slugs()
    for a in agents:
        aid = a.get("id")
        name = a.get("name") or aid or "assistant"
        if aid in excluded:
            out.append(f"skip (per-channel) {name}")
            continue
        if any(name.lower().startswith(p) for p in SKIP_NAME_PREFIXES):
            out.append(f"skip (supervisor-internal) {name}")
            continue
        meta = a.get("metadata_") or a.get("metadata") or {}
        blocks = {b.get("label"): b.get("value", "") for b in a.get("memory", {}).get("blocks", [])}
        slug = ar.mint_slug(name, taken)
        taken.add(slug)
        path = ar._path_for(slug, None)
        fm = ar._build_fm(
            name=name,
            purpose="",
            capabilities=[],
            triggers_list=trig.agent_triggers(meta),
            limits=dict(ar.DEFAULT_LIMITS),
            memory="letta",
            project_slug=None,
            letta_agent_id=aid,
        )
        out.append(_write_def(path, fm, blocks.get("persona", ""), apply))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate unified Agent definition files")
    ap.add_argument("--apply", action="store_true", help="write files (default: dry-run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== migrate_agent_defs ({mode}) ==")
    print("-- builders (from build_agents.json) --")
    for line in plan_builders(args.apply):
        print("  " + line)
    print("-- assistants (from Letta, per-channel + supervisor excluded) --")
    for line in plan_assistants(args.apply):
        print("  " + line)
    if not args.apply:
        print("\n(dry-run) re-run with --apply to write the definition files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
