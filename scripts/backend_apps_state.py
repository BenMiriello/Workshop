#!/usr/bin/env python3
"""What the RUNNING backend mounted, from which roster file, and whether that
agrees with the roster in this tree.

    python3 scripts/backend_apps_state.py          human-readable report
    python3 scripts/backend_apps_state.py --tsv    one TAB line, for apply.sh
    python3 scripts/backend_apps_state.py --json    the raw report plus verdict

WHY THIS EXISTS. On 2026-09-04 every app route on this installation answered
404 for two days while `/healthz` said `ok`, `apply.sh --check` said `in sync`,
and the launcher showed an empty shelf that is also what a correct fresh clone
shows. The source was fine: the same tree imported and registered every app
route. What differed was WHICH FILE the running process read as its
installed-apps roster -- that path is anchored to the module's own location, so
two checkouts of one source on one host resolve two different `.runtime/`
directories, and nothing printed either of them.

It could not be settled from outside, either. An agent in a sandbox does not
share the backend's mount namespace: `/proc/<pid>/environ` and `/proc/<pid>/cwd`
are unreadable, so every attempt to check the hypothesis measured the agent's
own filesystem and confirmed whatever the agent already believed.

So the process answers for itself. `/healthz` now carries an `apps` record --
the resolved roster path, the roster it read, the ids it actually mounted, and
the catalog it had to draw from -- pinned at mount time rather than recomputed
per request. This script reads that record and compares it against the roster
sitting in THIS tree, which is the comparison `apply.sh` was never able to make:
its backend check compares SOURCE hashes, and a roster is instance state that no
source hash covers.

WHAT IS AND IS NOT A FAULT. An empty roster with nothing mounted is a fresh
clone and is `ok`. A roster naming N apps with fewer than N mounted is an
outage. A process reading a different roster than this tree holds is the outage
that happened, and it is reported even when the process itself looks internally
consistent -- because it does, and that is the whole failure.

Output contract for --tsv is `state<TAB>headline<TAB>remedy`, matching
scripts/migration_state.py, because the caller is a shell script that formats
its own report lines.

  ok            the process mounted everything its roster names, and its roster
                agrees with this tree's (or this tree has none to compare)
  unresolved    the roster names apps the process did not mount: those routes
                are 404 right now
  roster_differs  the process read a different roster than this tree holds --
                the two-checkouts case, and the reason this file exists
  unreadable    the process could not read its roster at all
  unreported    the backend answers but does not carry an `apps` record: it is
                running code from before this existed. NOT "in sync"
  unreachable   nothing answered on the backend port

Exit status: 0 ok, 1 for a real disagreement, 2 for could-not-tell. Names no
app: every id it prints is read from a roster or from the running process.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HEALTH_URL = os.environ.get(
    "WORKSHOP_BACKEND_URL",
    f"http://127.0.0.1:{os.environ.get('WORKSHOP_BACKEND_PORT', '8000')}").rstrip("/") + "/healthz"
# Where THIS tree keeps its roster. Same default as
# `backend/services/installed_apps.REGISTRY_PATH`, resolved from this file
# instead of that one -- deliberately, because the point of the comparison is
# that the two can disagree.
TREE_ROSTER = Path(os.environ.get(
    "WORKSHOP_INSTALLED_APPS", str(REPO / ".runtime" / "installed-apps.json")))

INSTALL_CMD = "python3 scripts/install_apps.py"
RESTART_CMD = "bash scripts/apply.sh"


def fetch_health(timeout: float = 4.0) -> dict | None:
    """The running backend's /healthz, or None if nothing answered."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def tree_roster() -> tuple[list[str] | None, str | None]:
    """This tree's installed roster, and why it could not be read if it could not.

    An ABSENT file is not an error and not an empty roster: a linked worktree
    and a fresh clone both legitimately have none, and there is then simply
    nothing to compare the process against.
    """
    if not TREE_ROSTER.exists():
        return None, None
    try:
        data = json.loads(TREE_ROSTER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, f"{type(e).__name__}: {e}"
    ids = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return None, "no `installed` list"
    return [i for i in ids if isinstance(i, str)], None


def verdict(health: dict | None, roster: list[str] | None,
            roster_error: str | None) -> tuple[str, str, str]:
    """(state, headline, remedy). Pure, so the tests can drive every branch."""
    if health is None:
        return ("unreachable",
                f"apps: the backend did not answer {HEALTH_URL}, so what it "
                f"mounted is unknown",
                "start it, then rerun: bash scripts/apply.sh --check")

    rec = health.get("apps")
    if not isinstance(rec, dict) or rec.get("mounted") is None:
        return ("unreported",
                "apps: the running backend does not report what it mounted "
                "(it predates the `apps` record on /healthz), so a roster of "
                "apps serving nothing would still look healthy",
                f"restart it once to pick this up: {RESTART_CMD}")

    path = rec.get("registry") or "?"
    installed = list(rec.get("installed") or [])
    mounted = list(rec.get("mounted") or [])
    unresolved = list(rec.get("unresolved") or [])
    catalog = list(rec.get("catalog") or [])

    if rec.get("error"):
        return ("unreadable",
                f"apps: the running backend could not read its roster at {path} "
                f"({rec['error']})",
                "fix or remove that file, then restart the backend")

    if unresolved:
        why = ("its catalog is empty -- no app declaration resolved in that "
               "process at all" if not catalog
               else f"its catalog holds {', '.join(catalog)}")
        return ("unresolved",
                f"apps: the running backend read {len(installed)} installed "
                f"app(s) from {path} and mounted {len(mounted)}; "
                f"{', '.join(unresolved)} serve no routes and answer 404 "
                f"({why})",
                f"install the missing app(s) into the backend venv: {INSTALL_CMD}")

    if roster_error:
        return ("unreadable",
                f"apps: this tree's roster at {TREE_ROSTER} could not be read "
                f"({roster_error}), so it cannot be compared against the "
                f"{len(mounted)} app(s) the backend mounted",
                f'fix that file (an empty roster is `{{"installed": []}}`)')

    if roster is not None and sorted(roster) != sorted(installed):
        only_tree = sorted(set(roster) - set(installed))
        only_proc = sorted(set(installed) - set(roster))
        parts = []
        if only_tree:
            parts.append(f"this tree names {', '.join(only_tree)} and the "
                         f"backend did not read them")
        if only_proc:
            parts.append(f"the backend read {', '.join(only_proc)} and this "
                         f"tree does not name them")
        return ("roster_differs",
                f"apps: the running backend read its roster from {path} "
                f"({len(installed)} app(s)) and this tree's roster is "
                f"{TREE_ROSTER} ({len(roster)} app(s)) -- " + "; ".join(parts),
                "point the backend at this tree's roster (WORKSHOP_INSTALLED_APPS, "
                "or the service's WorkingDirectory) and restart it")

    if not installed:
        return ("ok",
                f"apps: none installed, none mounted (roster {path}) -- a blank "
                f"instance, which is a legitimate state",
                "")
    return ("ok",
            f"apps: {len(mounted)} of {len(installed)} mounted, "
            f"{rec.get('routes')} route(s), roster {path}",
            "")


EXIT = {"ok": 0, "unresolved": 1, "roster_differs": 1, "unreadable": 1,
        "unreported": 2, "unreachable": 2}


def main() -> None:
    args = set(sys.argv[1:])
    health = fetch_health()
    roster, roster_error = tree_roster()
    state, headline, remedy = verdict(health, roster, roster_error)

    if "--tsv" in args:
        print(f"{state}\t{headline}\t{remedy}")
    elif "--json" in args:
        print(json.dumps({
            "state": state,
            "headline": headline,
            "remedy": remedy,
            "backend": (health or {}).get("apps"),
            "backend_source_id": (health or {}).get("source_id"),
            "tree_roster_path": str(TREE_ROSTER),
            "tree_roster": roster,
        }, indent=2))
    else:
        rec = (health or {}).get("apps")
        print(f"backend:     {HEALTH_URL}")
        if isinstance(rec, dict):
            print(f"  roster:    {rec.get('registry')} "
                  f"({'present' if rec.get('registry_present') else 'ABSENT'})")
            print(f"  installed: {', '.join(rec.get('installed') or []) or '(none)'}")
            m = rec.get("mounted")
            print(f"  mounted:   "
                  f"{'(not recorded)' if m is None else (', '.join(m) or '(none)')}")
            print(f"  catalog:   {', '.join(rec.get('catalog') or []) or '(none)'}")
            print(f"  routes:    {rec.get('routes')}")
            if rec.get("error"):
                print(f"  error:     {rec['error']}")
        else:
            print("  (no apps record; this backend predates it or is not answering)")
        print(f"this tree:   {TREE_ROSTER}")
        print(f"  installed: "
              f"{'(no such file)' if roster is None and not roster_error else (roster_error or ', '.join(roster or []) or '(none)')}")
        print(f"{state}: {headline}")
        if remedy:
            print(f"  -> {remedy}")
    raise SystemExit(EXIT[state])


if __name__ == "__main__":
    main()
