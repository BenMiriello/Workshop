#!/usr/bin/env python3
"""No platform source file may name a specific app. Measured, ratcheted.

AND, since 2026-08-27, NO PLATFORM SOURCE FILE MAY NAME A FOLDER IN $HOME. That
is the fourth count (`home_path_sites`), and it exists because the first three
structurally could not see a whole class of the same defect: the notes app's
vault lived at a hardcoded `~/notes` in three platform files for months while
this script reported ZERO for all three of them, because the violation is not an
app NAME. The reasoning is at `_HOME_PATH_PATTERNS` below and in
docs/platform-redesign/memory-page-and-file-viewer-2026-08-27.md sections 1.2
and D12. If you are here because a count is failing, read that one first: a gate
that cannot see the violation is the thing this file is most against.

Rule (CLAUDE.md, and section 6 of
docs/platform-redesign/app-is-the-entity-2026-08-20.md): an app declares what it
is and the platform reads the declaration. A deploy for another user must
contain zero trace of this operator's apps.

WHY THIS EXISTS RATHER THAN TRUSTING THE OLDER PLUGGABLE-APPS GATE (retired
2026-09-04, kept for the record at scripts/retired/). That gate reported
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

DELTA MODE (`--since <ref>`), added 2026-08-23, and why it does not weaken the
rule. Everything above describes measuring EVERY tracked file against the
pinned baseline, and that is still exactly what a bare run does. But used that
way as a REVIEW GATE it judged a worker on the absolute value of debt the
worker inherited. This gate is UNCONDITIONAL in `scripts/reviewer.sh` -- on
purpose, because the gate it replaced was scoped to five files and reported
"clean" for five weeks -- so on 2026-08-23 it was failing (`literal_sites:
309 -> 314`) on EVERY run in the tree, including docs-only diffs that could not
possibly have added a literal. Each worker collected a rejection it could not
act on, retried, hit the no-progress thrash guard in `scripts/ralph.sh` and
exited 6. 2,066 loop jobs since 2026-07-18 all reported `tasks_done: 0`.

So with `--since <ref>` the script scans at `<ref>` (a `git archive` extract of
the base tree) AND on the working tree, and fails only on a count that got
worse BETWEEN THOSE TWO POINTS. It still scans EVERY tracked file on both
sides -- delta mode changes WHAT IS COMPARED, not what is measured. That is why
it is `--since` and not `--only-changed`: adding a literal to a file this diff
never touched (by moving code, or by adding an app to the roster) must still be
caught, and a per-file scoping would miss it.

ONE ROSTER FOR BOTH SIDES. The roster is computed once, from the working tree,
and used for BOTH scans. It has to be: it is drawn partly from
`.runtime/installed-apps.json`, which is untracked and therefore absent from
any archive extract, so re-deriving it on the base side would silently watch
fewer apps there, undercount the base, and report a regression that is really
just the roster shrinking underneath the scan. Same failure the `watched` list
in the baseline exists to prevent.

The pinned baseline still governs direction over time: a bare run still reports
the real absolute debt, `--write-baseline` still demands `--because` for an
upward pin, and `check_ratchets_not_loosened.py` still blocks re-pinning inside
a review. What delta mode removes is only this: blaming a run for debt that
already existed at its own base commit.

Usage:
    python3 scripts/check_no_app_names.py                 # report + gate on baseline
    python3 scripts/check_no_app_names.py --json
    python3 scripts/check_no_app_names.py --since HEAD    # gate mode: fail only on THIS run's regression
    python3 scripts/check_no_app_names.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "no_app_names_baseline.json"
# The sticky roster is a list of THIS installation's apps, so it lives beside the
# other instance registries in `.runtime/`, never in the tracked baseline: a
# re-pin used to write every installed app's name into a file every clone gets.
WATCHED = ROOT / ".runtime" / "no_app_names_watched.json"

# THE WHOLE TRACKED TREE IS SCANNED, since 2026-08-27.
#
# This was `PLATFORM_DIRS = ("backend/", "frontend/src/", "packages/",
# "scripts/", "infra/", "mobile/")` and everything outside it was skipped,
# on the reasoning that "prose naming an app is a historical record, not a
# build-time dependency". That reasoning is about `literals` and it was applied
# to all four counts, so `shared/`, `agents/`, `docs/`, `apps/`,
# `frontend/public/` and EVERY FILE AT THE REPO ROOT were never looked at by
# any of them -- including the home-path count added the same week precisely
# because a whole class of violation was invisible. A gate whose scan area is
# smaller than the rule's is the failure this file exists to stop.
#
# So the scan area is now the tracked tree, and only these are exempt, each
# because naming the thing IS its job:
_UNSCANNED = frozenset({
    # The gate's own memory: `watched` is a list of app names.
    "scripts/no_app_names_baseline.json",
    # The gate itself, which writes down the patterns it matches on and would
    # otherwise count its own explanation of what it counts.
    "scripts/check_no_app_names.py",
    # .gitignore's job is to name the instance paths it excludes -- the apps
    # are in it BECAUSE they are being separated. Same exemption, for the same
    # reason, that scripts/check_no_hardcoding.sh gives it.
    ".gitignore",
})

# Binary payloads. `read_text(errors="replace")` on a PNG produces mojibake that
# can match anything; a screenshot is not source naming an app.
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz", ".pdf",
    ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".mov",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".db", ".sqlite", ".pmtiles", ".bin",
})

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
        # A one-off script that belongs to an app is app source too, and a
        # stranger's clone has no more use for one operator's backfill than for
        # the app it backfills. Same NAMING rule as routes/services above, so a
        # script says whose it is; this is the path classifier deciding what IS
        # app source, not another pattern for detecting a leaked name.
        re.compile(rf"^backend/scripts/{short}(_[a-z_]+)?\.py$", re.I),
        re.compile(rf"^{a}/", re.I),
    )


# Modules whose name starts with an app id but which are PLATFORM code. Without
# this, `from routes import notes_all` reads as the notes APP, and main.py plus
# three memory tests get flagged for importing an app they do not import.
# routes/notes_all.py is the cross-scope knowledge feed; the notes APP is
# routes/notes.py and services/notes_vault.py.
_PLATFORM_MODULES = frozenset({"notes_all"})


def _is_platform_module_path(rel: str) -> bool:
    """Is this path one of the PLATFORM modules named in _PLATFORM_MODULES?

    Same roster the import check consults, applied to the path classification so
    the two cannot disagree about which files belong to an app.
    """
    m = re.fullmatch(r"backend/(?:routes|services)/([a-z_]+)\.py", rel)
    return bool(m and m.group(1) in _PLATFORM_MODULES)


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
    "location": (
        r"window\.location", r"\.location\b", r"file location", r"location of",
        # `packages/ui/src/native/*` (Stage 4b, 2026-08-20): the Workshop
        # native-bridge capability named "location" (background GPS tracking,
        # one of six on `WorkshopNative`, alongside audio/speech/localNotify/
        # liveActivity/secureStore), not the location APP. `has("location")`
        # and the `Capability` union member are the platform's own bridge
        # vocabulary, mirrored locally from shared/native-bridge.ts's
        # `CAP_METHODS` keys.
        r'has\("location"\)', r'\["location"\]', r'"location"\s*\|\s*"audio"',
        # `mobile/native/location.ts`: the Expo BACKGROUND TASK name for the
        # native bridge's location capability, not the location APP. Caught by
        # the constant-shape pattern added 2026-09-03 (an app-named constant
        # whose value also names it) and suppressed here for the same reason as
        # the entries above: this is the platform's own bridge vocabulary.
        r'LOCATION_TASK', r'workshop-bg-location',
    ),
    "budget": (r"token budget", r"budget\.remaining", r"wall[- ]clock budget", r"per-scope budget",
               r"injection budget", r"reconnect budget", r"budget ceiling",
               # The USAGE/spend budget, which is platform accounting and has
               # nothing to do with the budget APP. Both surfaced on 2026-09-03
               # as the only two false positives of the constant-shape pattern:
               # a config path for the usage ledger, and an agent's own token
               # allowance read from /agents/<id>/budget.
               r"WORKSHOP_USAGE_BUDGET", r"usage-budget", r"usage budget",
               r"/agents/[^\s\"']*/budget"),
    # `/api/notes` is the PLATFORM cross-scope knowledge feed
    # (routes/notes_all.py, prefix "/notes"). The notes APP is a different
    # router with a different prefix: routes/notes.py serves "/notes-app", and
    # that is what `.runtime/apps.json` lists as its only api_prefix. Without
    # this the Memory page is flagged for reading the knowledge store, which is
    # platform code calling a platform route. The negative lookahead keeps the
    # app's own prefix counted.
    # `router.*(get|api_route)("/notes"` is the backward-compat redirect in
    # routes/memory_compat.py that redirects the OLD PLATFORM route `/notes/...`
    # to the current `/api/memory/notes/...`. The notes-app's prefix is
    # `/notes-app`, not `/notes`. App-owned files are already excluded from the
    # scan, so this pattern only fires in platform backward-compat code.
    "notes": (r"notes_all", r"notes and docs", r"knowledge", r"release notes",
              r"/api/notes(?!-app)", r"""router\.\w+\("/notes[/"']"""),
    "environment": (r"environment variable", r"deployment target", r"\benv\b", r"environments are"),
    "commute": (),
    "birds": (),
    "finance": (),
    "plaid": (),
    "wearables": (),
}

# A literal only counts in one of these shapes. Free prose does not.
# ---- A FOURTH COUNT: platform source may not name a path in $HOME ----------
#
# WHY THIS EXISTS, and it is worth reading before touching it. The notes app's
# Markdown vault (`~/notes`) was hardcoded in three platform files for months
# while this script reported ZERO findings for all three, and the operator kept
# reporting the same problem and being told the counts were clean. Measured
# 2026-08-27 (memory-page-and-file-viewer-2026-08-27.md 1.2). Three independent
# blind spots, each sufficient on its own:
#
#   1. the alphabet is the app ids from `roster()`, and the literal is `notes`,
#      not `notes-app`;
#   2. `services/notes_vault.py` matches the app-CONTENT path patterns, so it is
#      filed as the app's own source and never inspected;
#   3. `notes` is on the `_AMBIGUOUS` suppression list, because it is an
#      ordinary English word.
#
# No amount of tuning the app alphabet finds it, because the violation is not an
# app NAME. It is a platform file reaching into a specific folder in the user's
# home directory -- which is the same class of defect (the platform knowing a
# fact it has no business knowing) wearing a shape this gate could not see.
#
# So this counts that shape directly, mechanically, and with its own baseline:
# `Path.home() / "<name>"`, `os.path.expanduser("~/<name>")`, and a `~/<name>`
# literal in a string. A path derived from config or an environment variable is
# not a finding; only a NAME written into platform source is.
_HOME_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Path.home() / "notes"   |   Path.home()/"notes"
    re.compile(r"""Path\.home\(\)\s*/\s*['"]([^'"/]+)['"]"""),
    # os.path.expanduser("~/notes")  |  expanduser('~/notes')
    re.compile(r"""expanduser\(\s*['"]~/([^'"]+)['"]"""),
    # a bare "~/notes" in a string, which is how it reaches the operator's screen
    re.compile(r"""['"]~/([A-Za-z0-9_.-]+)"""),
)

# `~/` on its own, and the paths this system legitimately owns: its own state
# lives under the operator's home by design, and naming it is not a dependency
# on somebody else's folder. Everything else is a finding.
_HOME_PATH_ALLOWED = frozenset({
    ".claude", ".config", ".local", ".cache", ".ssh", ".gitconfig",
    "workshop",
})

# AN EXPLICIT, NAMED TOOLCHAIN ALLOWLIST, added 2026-08-27 with the reason for
# each entry, and reported on every run rather than silently subtracted.
#
# The distinction that earns an entry here: is the path a fact about THIS
# INSTALLATION, or a fact about a TOOL that every installation of it has? The
# first is a violation no matter how convenient (`~/notes` is one operator's
# folder). The second is the documented location of a third-party tool's own
# config, identical on every machine that has the tool, and there is no config
# value that could sensibly replace it -- an env var pointing at the CLI's own
# user config would just be a second place to get it wrong.
#
# `_HOME_PATH_ALLOWED` above already exempts the `.claude` DIRECTORY, so
# `~/.claude/plugins` was never counted while `~/.claude.json` -- a sibling
# file, same tool, same argument -- was, which is an accident of one being a
# directory rather than a judgment about either. This makes the judgment
# explicit and keeps it visible: `main()` prints the allowlisted count, so a
# reader sees "0 findings, 3 allowlisted" and can go read why.
_HOME_PATH_TOOLCHAIN: dict[str, str] = {
    ".claude.json": (
        "the Claude Code CLI's own user-level config, holding the user scope "
        "and every project's local scope. services/mcp_registry.py reads it to "
        "show the operator which MCP servers a turn can reach, and "
        "settings/McpServers.tsx names it so the operator knows which file to "
        "go edit. Same path on every machine with the CLI installed; not a "
        "property of this install. Sibling of ~/.claude/, already allowed."
    ),
}

# `~/<literal>`, `Path.home() / "<name>"`: a doc explaining what this check
# matches, written with an angle-bracket placeholder. A real directory name
# cannot contain `<` or `>`, so this is mechanical, not a judgment call.
def _is_placeholder(name: str) -> bool:
    return "<" in name or ">" in name


def _home_path_names(line: str) -> list[str]:
    """The home-directory folder names a line hardcodes, if any.

    Deduped, because the patterns overlap on purpose: `Path.home() / "notes"`
    written as a string literal matches two of them, and one line reaching one
    folder is ONE finding. Counting it twice would make the ratchet's numbers
    depend on how the line happens to be spelled.
    """
    seen: dict[str, None] = {}
    for pat in _HOME_PATH_PATTERNS:
        for m in pat.finditer(line):
            name = m.group(1).strip().strip("/").split("/")[0]
            if not name or _is_placeholder(name):
                continue
            if name in _HOME_PATH_ALLOWED or name in _HOME_PATH_TOOLCHAIN:
                continue
            seen[name] = None
    return list(seen)


def _home_path_allowlisted(line: str) -> list[str]:
    """The toolchain-allowlisted home paths on a line. Reported, not gated."""
    seen: dict[str, None] = {}
    for pat in _HOME_PATH_PATTERNS:
        for m in pat.finditer(line):
            name = m.group(1).strip().strip("/").split("/")[0]
            if name in _HOME_PATH_TOOLCHAIN:
                seen[name] = None
    return list(seen)


def _literal_patterns(app: str) -> tuple[re.Pattern[str], ...]:
    a = re.escape(app)
    short = re.escape(app.removesuffix("-app"))
    return (
        re.compile(rf"""["']{a}["']"""),          # "birds"
        re.compile(rf"""["']app:{a}["']"""),      # "app:birds"
        re.compile(rf"""/api/{short}\b"""),       # /api/birds
        re.compile(rf"""["']/{short}["']"""),     # "/birds" route prefix
        re.compile(rf"""\bapps/{a}\b"""),         # apps/birds
        # A CONSTANT NAMED FOR AN APP, whose value also names it:
        #   NOTES_VAULT_ROOT = paths.env_path("WORKSHOP_NOTES_VAULT", "notes")
        #
        # Added 2026-09-03 after probing what the five shapes above actually
        # catch. They match where code ADDRESSES an app -- an id in a string, a
        # route, a path -- and a declaration named for one is none of those, so
        # the notes vault root sat in services/permissions.py, platform code
        # that never read it, invisible to this gate for as long as it existed.
        #
        # BOTH HALVES ARE REQUIRED, and that is what makes it usable. Matching
        # the NAME alone flags DEFAULT_INDEX_TOKEN_BUDGET, MCP_HEALTH_TTL_S and
        # LOCATION_TASK -- ten false positives out of eleven on this tree, every
        # one of them an ordinary English word in its platform sense. Requiring
        # the app's name in the VALUE too drops all ten and keeps the one real
        # site. Under-report rather than over-report: a number that is wrong
        # upward gets disbelieved and then ignored.
        re.compile(
            rf"""^\s*(?:export\s+)?(?:const\s+)?"""
            rf"""(?=[A-Z0-9_]*{short.upper()})[A-Z][A-Z0-9_]*"""
            rf"""\s*[:=][^\n]*(?i:\b{short}\b)""",
            re.M,
        ),
    )


def tracked_files(root: Path = ROOT) -> list[str]:
    """Repo-relative paths of every tracked file under `root`.

    For this checkout that is `git ls-files`. For a `git archive` EXTRACT of a
    base ref (delta mode) there is no `.git`, so the walk IS the answer: an
    archive contains exactly the tracked tree at that ref and nothing else --
    no working-tree edits, no untracked files, no ignored files. The two paths
    therefore yield the same kind of list, which is what lets one `scan()`
    serve both sides."""
    if (root / ".git").exists():
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        return [ln for ln in out.splitlines() if ln]
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


# The platform's OWN project, which is not an app and must not be scanned as
# one. This installation hosts its own source as a project, so the slug appears
# in the same registry the apps do -- but it names the platform, and `git grep`
# for it hits 251 tracked files, almost all of them the ordinary English word.
# Scanning it would bury the real count under noise, which is the failure mode
# this file's header calls out: a number that is wrong upward gets disbelieved
# and then ignored.
_PLATFORM_SCOPES = frozenset({"development"})


def roster(baseline: dict) -> list[str]:
    """Every app this installation knows about, from EVERY place one is declared.

    THE ROSTER IS THE WHOLE GATE. An app that is not on it is not scanned, so
    its name in platform source counts zero and the total reads clean for a
    violation that is sitting right there. That is not hypothetical: on
    2026-09-03 this function read one registry, an app was declared only in
    another, and seven platform files naming it counted nothing.

    Three sources, unioned, because this tree has three registries that do not
    agree with each other (app-is-the-entity-2026-08-20.md section 9 is the
    merge, and it is not built):

      1. the app DECLARATIONS in the tree  -- what the catalog can build
      2. `.runtime/installed-apps.json`    -- what is mounted here
      3. `.runtime/projects.json`          -- what has a working tree here

    Plus `watched`, which is sticky on purpose so an app that leaves the
    catalog is still scanned for the leftovers it left behind. It is read from
    `.runtime/no_app_names_watched.json` (instance-local) and from the
    baseline's own `watched` key, which a tracked baseline leaves empty.
    """
    names: set[str] = set(baseline.get("watched", []))
    if WATCHED.exists():
        try:
            names.update(json.loads(WATCHED.read_text()).get("watched", []))
        except (ValueError, OSError, AttributeError):
            pass

    # 1. The declarations, wherever they currently are.
    #
    # THIS SOURCE HAS NOW GONE DEAD TWICE, the same way both times: it read a
    # place declarations used to be, kept returning nothing, and said nothing
    # about it. First it parsed `_CATALOG_FACTORIES` out of app_registry.py,
    # removed 2026-09-01. Then it globbed `backend/services/*_app.py`, which
    # matched zero files from 2026-09-04, when every app left this repo -- the
    # extraction this gate exists to measure is the event that blinded it.
    #
    # So it reads BOTH places now and asserts it found something. An app that
    # is not on the roster is not scanned, and its name in platform source
    # counts zero: a roster that quietly empties turns this gate into a
    # function that returns "clean" for every input.
    found_declarations = 0
    for decl in sorted((ROOT / "backend" / "services").glob("*_app.py")):
        body = decl.read_text(errors="replace")
        m = re.search(r"""\bid\s*=\s*["']([a-z0-9][a-z0-9-]*)["']""", body)
        if m:
            names.add(m.group(1))
            found_declarations += 1

    # An EXTRACTED app declares itself in its own manifest, which is the file
    # the platform already reads to render its card.
    apps_dir = ROOT / "apps"
    if apps_dir.is_dir():
        for manifest in sorted(apps_dir.glob("*/workshop.app.json")):
            try:
                name = (json.loads(manifest.read_text()) or {}).get("name")
            except (ValueError, OSError):
                continue          # a broken manifest is the app's problem
            if name:
                names.add(name)
                found_declarations += 1

    if not found_declarations:
        print("check_no_app_names: WARNING -- no app DECLARATION was found in "
              "backend/services/*_app.py or apps/*/workshop.app.json. The roster "
              "is running on `watched` alone, so an app installed since the last "
              "re-pin is NOT being scanned. This is how the gate went blind on "
              "2026-09-03; fix the source rather than trusting the count.",
              file=sys.stderr)

    # 2 and 3. The two instance registries.
    installed = ROOT / ".runtime" / "installed-apps.json"
    if installed.exists():
        try:
            names.update(json.loads(installed.read_text()).get("installed", []))
        except (ValueError, OSError):
            pass

    projects = ROOT / ".runtime" / "projects.json"
    if projects.exists():
        try:
            data = json.loads(projects.read_text())
            entries = data.get("projects", data)
            if isinstance(entries, dict):
                names.update(entries.keys())
            elif isinstance(entries, list):
                names.update(e.get("slug") for e in entries if isinstance(e, dict))
        except (ValueError, OSError, AttributeError):
            pass

    return sorted(n for n in names if n and n not in _PLATFORM_SCOPES)


def scan(apps: list[str], root: Path = ROOT) -> dict:
    """Scan the tree under `root` for the given app roster.

    `apps` is passed IN rather than re-derived per tree, so delta mode scans
    both sides with the identical roster -- see this script's header."""
    files = tracked_files(root)
    content_pat = {a: _app_content_patterns(a) for a in apps}
    import_short = {a: re.escape(a.removesuffix("-app")) for a in apps}
    literal_pat = {a: _literal_patterns(a) for a in apps}
    # Keyed by the full app id AND by the short form the frontend uses, because
    # they differ for exactly the app whose name is an ordinary English word:
    # the id is `notes-app`, the short form is `notes`, and the table is written
    # under `notes`. Looking up only the full id meant that entry never applied
    # to anything, so `notes_all` and the platform knowledge route were counted
    # as naming the notes APP for as long as the entry has existed.
    ambiguous = {
        a: [re.compile(p, re.I)
            for p in (*_AMBIGUOUS.get(a, ()), *_AMBIGUOUS.get(a.removesuffix("-app"), ()))]
        for a in apps
    }

    content: dict[str, list[str]] = {a: [] for a in apps}
    imports: list[dict] = []
    literals: list[dict] = []
    home_paths: list[dict] = []
    home_allowlisted: list[dict] = []

    for rel in files:
        # The three files whose job is to name these things -- see _UNSCANNED.
        if rel in _UNSCANNED:
            continue
        if Path(rel).suffix.lower() in _BINARY_SUFFIXES:
            continue
        owner = next((a for a in apps if any(p.search(rel) for p in content_pat[a])), None)
        if owner and _is_platform_module_path(rel):
            # A PLATFORM module whose NAME merely starts like an app's. The
            # import check has always known the difference (_PLATFORM_MODULES);
            # this classification did not, so `routes/notes_all.py` -- the
            # cross-scope knowledge feed, platform code by every other measure
            # -- was counted as one app's source. That is not just a wrong
            # number: check_fresh_clone.sh DELETES exactly these paths to prove
            # the platform builds without any app, so it was deleting a platform
            # route module, and `import main` died on it. Found 2026-09-02, once
            # the frontend half of that assertion started passing and stopped
            # masking the backend half.
            owner = None
        if owner:
            content[owner].append(rel)
        # NO `continue` HERE, since 2026-08-27, and this is the whole point of
        # the fix. Being app source used to exempt a file from ALL FOUR checks
        # when it should only ever have exempted it from ITS OWN NAME. That is
        # how `services/notes_vault.py` held a hardcoded `~/notes` while the
        # home-path count -- added the same week specifically to catch `~/notes`
        # -- reported it clean: the file was filed as the notes app's own
        # source and then never opened. So an app-owned file is read like any
        # other, and only the `app == owner` comparisons below are skipped: an
        # app naming ITSELF is not a violation, an app naming ANOTHER app is.
        path = root / rel
        try:
            body = path.read_text(errors="replace")
        except OSError:
            continue
        if len(body) > 2_000_000:  # generated bundles, lockfiles
            continue

        for app in apps:
            if app == owner:
                continue
            if _imports_app_module(body, import_short[app]):
                imports.append({"file": rel, "app": app})

        for i, line in enumerate(body.splitlines(), 1):
            stripped = line.strip()
            # A comment is prose, and prose is not a dependency. `/*` and `/**`
            # are here because a JSDoc block's OPENING line starts with a slash,
            # not a star, so it was the one comment line in every such block
            # that got scanned as code.
            if stripped.startswith(("#", "//", "*", "/*")):
                continue
            # A hardcoded folder in the user's home directory. Checked on the
            # SAME non-comment lines as the literals above, and deliberately
            # NOT suppressed by `_AMBIGUOUS`: the whole reason this count
            # exists is that the suppression list is what hid it.
            for name in _home_path_names(line):
                home_paths.append({"file": rel, "line": i, "name": name})
            for name in _home_path_allowlisted(line):
                home_allowlisted.append({"file": rel, "line": i, "name": name})

            for app in apps:
                if app == owner:  # an app's own file naming itself
                    continue
                if not any(p.search(line) for p in literal_pat[app]):
                    continue
                if any(p.search(line) for p in ambiguous[app]):
                    continue
                literals.append({"file": rel, "line": i, "app": app})

    return {
        "watched": apps,
        "content_files": sum(len(v) for v in content.values()),
        "content_by_app": {a: len(v) for a, v in sorted(content.items()) if v},
        # The PATHS behind `content_files`, not just the count. Added 2026-09-01
        # for check_fresh_clone.sh's build_with_zero_apps assertion, which has to
        # DELETE the app source before building and must delete exactly what this
        # classifier calls app source. Anything else would be a second definition
        # of "app source" living in a shell script, which is the duplication
        # check_one_definition.py exists to catch.
        "content_paths": sorted(p for v in content.values() for p in v),
        "import_sites": len(imports),
        "literal_sites": len(literals),
        "home_path_sites": len(home_paths),
        "home_path_allowlisted": len(home_allowlisted),
        "imports": imports,
        "literals": literals,
        "home_paths": home_paths,
        "home_allowlisted": home_allowlisted,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--list-content",
        action="store_true",
        help="Print the tracked paths counted by `content` (the files that ARE "
             "app source), one per line, and exit. The list form of the number; "
             "check_fresh_clone.sh deletes exactly these before building.",
    )
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument(
        "--since",
        metavar="REV",
        default=None,
        help="DELTA MODE: scan REV's tree as well, and fail only on a count that "
             "got worse between REV and the working tree. The pinned baseline is "
             "still reported. See this script's header.",
    )
    ap.add_argument(
        "--because",
        default="",
        help="Required when a pin RAISES a count. Recorded in the baseline and "
             "printed on every subsequent run, so an upward pin cannot be quiet.",
    )
    args = ap.parse_args()

    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    # ONE roster for both sides -- see this script's header for why re-deriving
    # it inside the extract would undercount the base tree.
    apps = roster(base)
    result = scan(apps, ROOT)

    # DELTA MODE. `since_counts` becomes what the gate judges against; the
    # pinned baseline is still compared and still printed, so inherited debt is
    # never hidden just because this run happened not to add to it.
    since_counts = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, ROOT) as base_tree:
                since_counts = scan(apps, base_tree)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_no_app_names: cannot read ref {args.since!r}: {exc}",
                   file=sys.stderr)
            return 2

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
            for k in ("content_files", "import_sites", "literal_sites", "home_path_sites")
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
                "go DOWN. The target for all four is zero: content reaches it at "
                "extraction (stage 7), imports at the AppSpec seams (stage 4), "
                "home_path_sites when every path in $HOME comes from config. "
                "`watched` is sticky on purpose, so an app that leaves the catalog "
                "is still scanned for leftovers; it names this installation's "
                "apps, so it is kept in .runtime/no_app_names_watched.json. "
                "NOTE 2026-08-27: content/imports/literals were re-pinned at a "
                "HIGHER measured value on this date, once, because the scan area "
                "was widened from six directories to the tracked tree and an "
                "app-content file stopped short-circuiting out of all four checks. "
                "Nobody added those lines; the gate could not previously see them. "
                "See `raised` below for the full reason, which prints on every run "
                "until each count comes back down."
            ),
            "watched": [],
            "content_files": result["content_files"],
            "import_sites": result["import_sites"],
            "literal_sites": result["literal_sites"],
            "home_path_sites": result["home_path_sites"],
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
        WATCHED.parent.mkdir(parents=True, exist_ok=True)
        WATCHED.write_text(json.dumps({"watched": result["watched"]}, indent=2) + "\n")
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        for k, v in sorted(payload.get("raised", {}).items()):
            print(f"  RAISED {k} {v['from']} -> {v['to']}: {v['because']}")
        return 0

    if args.list_content:
        for rel in result["content_paths"]:
            print(rel)
        return 0

    if args.json:
        payload = dict(result)
        if since_counts is not None:
            payload["since_ref"] = args.since
            payload["since"] = {
                k: since_counts[k]
                for k in ("content_files", "import_sites", "literal_sites", "home_path_sites")
            }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"app-names: watching {len(result['watched'])} apps: {', '.join(result['watched'])}")
    print(f"  content  {result['content_files']:>4}  tracked files that ARE app source")
    for app, n in sorted(result["content_by_app"].items(), key=lambda kv: -kv[1]):
        print(f"             {n:>4}  {app}")
    print(f"  imports  {result['import_sites']:>4}  platform files importing an app module")
    print(f"  literals {result['literal_sites']:>4}  platform lines naming an app")
    print(f"  homepath {result['home_path_sites']:>4}  platform lines hardcoding a folder in $HOME")
    if result.get("home_path_allowlisted"):
        names = sorted({h["name"] for h in result["home_allowlisted"]})
        print(f"           +{result['home_path_allowlisted']:>3}  allowlisted toolchain path(s) "
              f"NOT counted: {', '.join(names)}")
        print( "                 (reason per entry in _HOME_PATH_TOOLCHAIN; "
               "these are a tool's own config, not this install's)")

    if not base:
        print("\nno baseline; run --write-baseline to pin one")
        return 0

    # A recorded upward pin is repeated on every run until it is paid off, so a
    # count that went the wrong way stays visible instead of becoming the floor.
    for key, info in sorted((base.get("raised") or {}).items()):
        print(f"  note {key} was RAISED {info['from']} -> {info['to']}: {info['because']}")

    judge_against = since_counts if since_counts is not None else base
    against = f"the base ref {args.since}" if since_counts is not None else "the pinned baseline"

    failed = False
    for key in ("content_files", "import_sites", "literal_sites", "home_path_sites"):
        was, now = judge_against.get(key), result[key]
        if was is None:
            continue
        if now > was:
            print(f"\nFAIL {key}: {was} -> {now} against {against}. "
                  f"The rule is that this only goes down.")
            failed = True
        elif now < was:
            # "improved", NOT "ok", AND THE DISTANCE FROM ZERO IS PRINTED WITH IT.
            #
            # This line used to read "ok  literal_sites improved 479 -> 470",
            # and "ok" is the word every reader takes away. The target for all
            # four counts is zero (see this file's header and the baseline's own
            # comment), but nothing here has ever failed for being ABOVE zero:
            # the gate only fails when a number goes UP. So a tree with 470
            # platform lines naming a specific app, and 106 tracked files that
            # ARE app source, reports green, and has for months.
            #
            # That is the shape `check_fresh_clone.sh` already avoids and this
            # one did not: "a pin there prints NOT YET, never PASS, and never
            # makes the absolute run green." Reported 2026-09-01: "how the fuck
            # is it still happening?" It is still happening because the
            # instrument said ok.
            print(f"  improved  {key} {was} -> {now} against {against}"
                  f"  (NOT YET: {now} to remove, the target is 0)")

    # Inherited debt is REPORTED in delta mode, never gated on. A count that is
    # above the pinned baseline but unchanged by this run is somebody's problem,
    # just not this run's, and it must not go quiet.
    if since_counts is not None and base:
        stale = [
            f"{k}: pinned {base[k]} -> now {result[k]}"
            for k in ("content_files", "import_sites", "literal_sites", "home_path_sites")
            if base.get(k) is not None and result[k] > base[k]
        ]
        if stale:
            print(f"\n  FYI {len(stale)} count(s) still past the PINNED baseline, "
                  f"inherited and not caused by this change (not gating):")
            for line in stale:
                print(f"      {line}")

    # THE STANDING DISTANCE FROM ZERO, printed on every run that is not gating,
    # so no summary can read this check as done while the violation is there.
    # It does not change the exit code: this is a ratchet and turning it into a
    # hard failure at 470 would just get it switched off. What it removes is the
    # ability to glance at the output and conclude the work landed.
    remaining = {k: result[k] for k in
                 ("content_files", "import_sites", "literal_sites", "home_path_sites")
                 if result[k] > 0}
    if remaining and not failed:
        total = sum(remaining.values())
        print(f"\n  NOT YET: {total} app-specific site(s) remain in platform "
              f"source; the target for every count is 0.")
        for k, v in remaining.items():
            print(f"      {k}: {v}")
        print("      This check going green means NO WORSE, never done. "
              "app-is-the-entity-2026-08-20.md section 9 is the removal.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
