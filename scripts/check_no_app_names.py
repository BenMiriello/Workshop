#!/usr/bin/env python3
"""No platform source file may name a specific app. Measured, ratcheted.

Rule (CLAUDE.md, and section 6 of
docs/platform-redesign/app-is-the-entity-2026-08-20.md): an app declares what it
is and the platform reads the declaration. A deploy for another user must
contain zero trace of this operator's apps.

WHY THIS EXISTS RATHER THAN TRUSTING check_pluggable_apps.sh. That gate reports
`pluggable-apps: clean` and inspects FIVE files: main.py, llm_router.py,
chat_context.py, app_registry.py, Apps.tsx. It never looks at
frontend/src/pages/apps/ (61 tracked files), apps/ (43), backend/mcp/plaid/,
agents/apps/, or the 42 app-named modules in backend/services and backend/routes.
So it stayed green for five weeks while the violation grew. A gate that cannot
see the violation is worse than no gate: it turns an unsolved problem into a
solved-looking one. See section 0.2 of
docs/platform-redesign/app-separation-and-github-2026-08-20.md.

WHERE THE ROSTER COMES FROM, and why it is not typed here. Hardcoding app names
in the guard is the exact mistake being corrected -- it misses the app someone
just added, which is the only case that matters. The roster is the union of:

  1. the built-in catalog       app ids in app_registry._CATALOG_FACTORIES
  2. the per-instance install   .runtime/installed-apps.json
  3. every app already recorded in the baseline

(3) is what makes this a ratchet rather than a moving target. An app that leaves
the catalog during extraction stays WATCHED, so leftovers cannot hide behind the
roster shrinking underneath the scan. Once an app is known, it is known forever.

WHAT COUNTS AS A VIOLATION. Three categories, deliberately different in kind,
because they need different fixes and go to zero at different stages:

  content  a tracked file that IS app source sitting in the platform repo
           (frontend/src/pages/apps/**, apps/**, backend/mcp/<app>/**,
           agents/apps/<app>.md, backend/{routes,services}/<app>*.py).
           Fixed by extraction. Path-based, so zero false positives.

  imports  a PLATFORM file that structurally imports an app module.
           Fixed by the AppSpec seams. Import-shaped, so zero false positives.

  literals a platform file naming an app as a string/path literal.
           Fixed case by case.

Categories `content` and `imports` are path- and syntax-shaped and cannot
misfire. `literals` is the fuzzy one: several app ids are also ordinary English
the platform uses legitimately (`health` in healthz, `location` in
window.location, `budget` in token budget, `notes` in the knowledge store,
`environment` in every sense). So a literal only counts in a quoted-string or
path position, and _AMBIGUOUS carries the platform senses that are excluded.
Under-reporting there is deliberate: a number that is wrong upward gets
disbelieved and then ignored, which is how the last gate died.

PIN FROM A COMMITTED TREE. The counts come from `git ls-files`, so pinning with
work in progress records whatever the index happened to hold: the first baseline
was written mid-edit and read 313/143 when the committed tree was 331/144, which
then failed the ratchet on the next run for no reason. Commit, then --write-baseline.

Usage:
    python3 scripts/check_no_app_names.py                 # report + gate on baseline
    python3 scripts/check_no_app_names.py --json
    python3 scripts/check_no_app_names.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "no_app_names_baseline.json"

# Directories that are platform source. A file outside all of these (docs/, a
# dated handoff, a template) is not gated: prose naming an app is a historical
# record, not a build-time dependency.
PLATFORM_DIRS = ("backend/", "frontend/src/", "packages/", "scripts/", "infra/", "mobile/")

# Paths that ARE app source. Anything matching is `content`, never `literals`,
# so an app's own file is not also counted for naming itself.
def _app_content_patterns(app: str) -> tuple[re.Pattern[str], ...]:
    a = re.escape(app)
    # Budget -> "Budget.tsx"; notes-app -> "notes"; the frontend drops the suffix.
    short = re.escape(app.removesuffix("-app"))
    return (
        re.compile(rf"^frontend/src/pages/apps/{short}(/|\.tsx$|\.ts$)", re.I),
        re.compile(rf"^apps/{a}/", re.I),
        re.compile(rf"^backend/mcp/{a}/", re.I),
        re.compile(rf"^agents/apps/{a}\.md$", re.I),
        re.compile(rf"^backend/(routes|services)/{short}(_[a-z_]+)?\.py$", re.I),
        re.compile(rf"^backend/services/{short}/", re.I),
        re.compile(rf"^{a}/", re.I),
    )


# Modules whose name starts with an app id but which are PLATFORM code. Without
# this, `from routes import notes_all` reads as the notes APP, and main.py plus
# three memory tests get flagged for importing an app they do not import.
# routes/notes_all.py is the cross-scope knowledge feed; the notes APP is
# routes/notes.py and services/notes_vault.py.
_PLATFORM_MODULES = frozenset({"notes_all"})


def _imports_app_module(body: str, short: str) -> bool:
    """Does this file import a module BELONGING to the app, as opposed to one
    whose name merely starts the same way?

    Matched by module NAME and then filtered, rather than by a substring of the
    import line. A prefix match cannot tell `notes_vault` (the app) from
    `notes_all` (the platform), and guessing wrong in the permissive direction
    is how a gate's numbers stop being believed.
    """
    names: set[str] = set()
    for m in re.finditer(r"^\s*from\s+(?:routes|services)\s+import\s+(.+)$", body, re.M):
        names.update(n.strip().split(" as ")[0].strip() for n in m.group(1).split(","))
    for m in re.finditer(r"^\s*(?:from|import)\s+(?:routes|services)\.(\w+)", body, re.M):
        names.add(m.group(1))
    if any(
        n and n not in _PLATFORM_MODULES and re.fullmatch(rf"{short}(_\w+)?", n) for n in names
    ):
        return True
    # TS: an app page import. Written with a placeholder, not a real app name:
    # a real one here makes this file trip its own matcher, which it did.
    return bool(re.search(rf"""from\s+["'][^"']*pages/apps/{short}[^"']*["']""", body, re.I))


# App ids that are also ordinary platform English. The pattern is the PLATFORM
# sense, excluded from `literals`. Each was verified against the tree.
_AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "health": (r"healthz", r"health[_-]?check", r"healthy", r"mcp[_-]health", r"auth[_-]health"),
    "location": (r"window\.location", r"\.location\b", r"file location", r"location of"),
    "budget": (r"token budget", r"budget\.remaining", r"wall[- ]clock budget", r"per-scope budget",
               r"injection budget", r"reconnect budget", r"budget ceiling"),
    "notes": (r"notes_all", r"notes and docs", r"knowledge", r"release notes"),
    "environment": (r"environment variable", r"deployment target", r"\benv\b", r"environments are"),
    "commute": (),
    "birds": (),
    "finance": (),
    "plaid": (),
    "wearables": (),
}

# A literal only counts in one of these shapes. Free prose does not.
def _literal_patterns(app: str) -> tuple[re.Pattern[str], ...]:
    a = re.escape(app)
    short = re.escape(app.removesuffix("-app"))
    return (
        re.compile(rf"""["']{a}["']"""),          # "birds"
        re.compile(rf"""["']app:{a}["']"""),      # "app:birds"
        re.compile(rf"""/api/{short}\b"""),       # /api/birds
        re.compile(rf"""["']/{short}["']"""),     # "/birds" route prefix
        re.compile(rf"""\bapps/{a}\b"""),         # apps/birds
    )


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ln for ln in out.splitlines() if ln]


def roster(baseline: dict) -> list[str]:
    """Catalog + install list + everything the baseline already watches."""
    names: set[str] = set(baseline.get("watched", []))

    registry = ROOT / "backend" / "services" / "app_registry.py"
    if registry.exists():
        body = registry.read_text(errors="replace")
        block = re.search(r"_CATALOG_FACTORIES\s*=\s*\((.*?)\n\)", body, re.S)
        if block:
            names.update(re.findall(r"""\(\s*["']([a-z0-9][a-z0-9-]*)["']""", block.group(1)))

    installed = ROOT / ".runtime" / "installed-apps.json"
    if installed.exists():
        try:
            names.update(json.loads(installed.read_text()).get("installed", []))
        except (ValueError, OSError):
            pass

    return sorted(n for n in names if n)


def scan(apps: list[str]) -> dict:
    files = tracked_files()
    content_pat = {a: _app_content_patterns(a) for a in apps}
    import_short = {a: re.escape(a.removesuffix("-app")) for a in apps}
    literal_pat = {a: _literal_patterns(a) for a in apps}
    ambiguous = {a: [re.compile(p, re.I) for p in _AMBIGUOUS.get(a, ())] for a in apps}

    content: dict[str, list[str]] = {a: [] for a in apps}
    imports: list[dict] = []
    literals: list[dict] = []

    for rel in files:
        # The gate's own memory is not a violation. `watched` in the baseline is
        # a list of app names, it lives under scripts/, and without this the
        # ratchet counts itself: eight literals that can only ever be removed by
        # forgetting which apps to watch for.
        if rel == str(BASELINE.relative_to(ROOT)):
            continue
        owner = next((a for a in apps if any(p.search(rel) for p in content_pat[a])), None)
        if owner:
            content[owner].append(rel)
            continue  # app source naming itself is not a platform violation
        if not rel.startswith(PLATFORM_DIRS):
            continue
        path = ROOT / rel
        try:
            body = path.read_text(errors="replace")
        except OSError:
            continue
        if len(body) > 2_000_000:  # generated bundles, lockfiles
            continue

        for app in apps:
            if _imports_app_module(body, import_short[app]):
                imports.append({"file": rel, "app": app})

        for i, line in enumerate(body.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*")):
                continue  # a comment is prose, and prose is not a dependency
            for app in apps:
                if not any(p.search(line) for p in literal_pat[app]):
                    continue
                if any(p.search(line) for p in ambiguous[app]):
                    continue
                literals.append({"file": rel, "line": i, "app": app})

    return {
        "watched": apps,
        "content_files": sum(len(v) for v in content.values()),
        "content_by_app": {a: len(v) for a, v in sorted(content.items()) if v},
        "import_sites": len(imports),
        "literal_sites": len(literals),
        "imports": imports,
        "literals": literals,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument(
        "--because",
        default="",
        help="Required when a pin RAISES a count. Recorded in the baseline and "
             "printed on every subsequent run, so an upward pin cannot be quiet.",
    )
    args = ap.parse_args()

    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    result = scan(roster(base))

    if args.write_baseline:
        # AN UPWARD PIN MUST SAY WHY. A ratchet you re-pin whenever it fails is
        # not a ratchet, and that is precisely how the gate this one replaced
        # died: it went green and nobody asked what it was measuring. There IS a
        # legitimate upward move -- a stage that lifts an app's logic out of a
        # platform module into an app-owned file raises `content` (more app
        # files in the repo) while lowering `imports` and `literals` (fewer
        # platform files that know an app exists), and that is progress. So the
        # rise is allowed, and it is allowed only with a recorded reason that
        # prints on every run until the count comes back down.
        raised = {
            k: (base.get(k), result[k])
            for k in ("content_files", "import_sites", "literal_sites")
            if base.get(k) is not None and result[k] > base[k]
        }
        if raised and not args.because:
            for k, (was, now) in sorted(raised.items()):
                print(f"pin would RAISE {k}: {was} -> {now}")
            print("\nRefusing. Re-pin an increase with --because '<why>'.")
            return 1
        payload = {
            "_comment": (
                "Ratchet baseline for check_no_app_names.py. These counts may only "
                "go DOWN. The target for all three is zero: content reaches it at "
                "extraction (stage 7), imports at the AppSpec seams (stage 4). "
                "`watched` is sticky on purpose, so an app that leaves the catalog "
                "is still scanned for leftovers."
            ),
            "watched": result["watched"],
            "content_files": result["content_files"],
            "import_sites": result["import_sites"],
            "literal_sites": result["literal_sites"],
        }
        if raised:
            payload["raised"] = {
                k: {"from": was, "to": now, "because": args.because}
                for k, (was, now) in sorted(raised.items())
            }
        elif base.get("raised"):
            # Carry a prior reason forward only for counts still above where the
            # reason was recorded; a count that came back down has been paid off.
            keep = {
                k: v for k, v in base["raised"].items()
                if result.get(k, 0) > v.get("from", 0)
            }
            if keep:
                payload["raised"] = keep
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        for k, v in sorted(payload.get("raised", {}).items()):
            print(f"  RAISED {k} {v['from']} -> {v['to']}: {v['because']}")
        return 0

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"app-names: watching {len(result['watched'])} apps: {', '.join(result['watched'])}")
    print(f"  content  {result['content_files']:>4}  tracked files that ARE app source")
    for app, n in sorted(result["content_by_app"].items(), key=lambda kv: -kv[1]):
        print(f"             {n:>4}  {app}")
    print(f"  imports  {result['import_sites']:>4}  platform files importing an app module")
    print(f"  literals {result['literal_sites']:>4}  platform lines naming an app")

    if not base:
        print("\nno baseline; run --write-baseline to pin one")
        return 0

    # A recorded upward pin is repeated on every run until it is paid off, so a
    # count that went the wrong way stays visible instead of becoming the floor.
    for key, info in sorted((base.get("raised") or {}).items()):
        print(f"  note {key} was RAISED {info['from']} -> {info['to']}: {info['because']}")

    failed = False
    for key in ("content_files", "import_sites", "literal_sites"):
        was, now = base.get(key), result[key]
        if was is None:
            continue
        if now > was:
            print(f"\nFAIL {key}: {was} -> {now}. The rule is that this only goes down.")
            failed = True
        elif now < was:
            print(f"  ok  {key} improved {was} -> {now} (re-pin with --write-baseline)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
