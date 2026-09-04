#!/usr/bin/env python3
"""Surface nothing can reach, measured and ratcheted.

WHY THIS EXISTS. Every invariant in this repo that actually holds is enforced
by something that fails the build. One is not: "a change deletes the surface it
replaces in the same commit that adds the new one"
(docs/platform-redesign/threads-branches-and-pages-2026-08-29.md, section 7).
It is enforced by prose, and it is the only one that has never held. Measured
2026-09-01: of 22 retirement commitments across the four current surface docs,
1 landed; five design generations are routed at once; and the page marked for
deletion GREW BY TWO LINES while marked. See
docs/platform-redesign/WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md section 5.1.

Deleting a module is invisible and safe, so it does not happen. This file makes
NOT deleting it visible. It does not decide what should go -- a replacement
decision is a person's job -- it only refuses to let the pile grow.

WHAT IS MEASURED. Four counts, each with a per-item baseline, so a NEW dead
item fails even when the total went down. That per-item shape is also why this
checker does not need `--since` to be fair about inherited debt (it accepts the
flag because scripts/reviewer.sh passes it, and uses it for the three
text-derived counts): an item already in the baseline blames nobody.

  1. unreferenced_frontend_modules -- a .ts/.tsx file under frontend/src or
     packages/ui that no entry point reaches. MEASURED BY `knip`, not by a
     hand-rolled grep, and that is deliberate: the audit that motivated this
     file caught an existing gate that was blind to `lazyPage(() =>
     import(...))`, and a regex over import statements is exactly how that
     happens. knip resolves the real TypeScript module graph, static and
     dynamic. Entry points are declared in knip.json at the repo root.
     If knip is not installed the count reports `unavailable` rather than 0 --
     a gate that cannot see the violation must never report clean.

  2. top_level_routes_absent_from_nav -- a depth-1, parameter-free React route
     that appears in no navigation list. This is the count the other three
     cannot produce: `/marketplace` and `/stores` are imported by App.tsx and
     served by the router, so nothing about the module graph says they are
     dead, and yet nothing in the product points at them. The settled design
     doc uses this exact vocabulary for them ("unreachable from nav today",
     section 6) and for `/runs` ("already unreachable from nav").
     Each entry also carries `inbound`, the forward links that DO point at it,
     so a reader can tell "nothing at all points here" from "one contextual
     link on a page that is itself queued for deletion".
     A `back={...}` slot is NOT a forward link. A record page's back button
     points at its own list, so counting it would let every list page vouch
     for itself: that is precisely how `/stores` looked reachable.

  3. unimported_backend_modules -- a module under backend/ that no non-test
     Python file imports (AST, absolute and relative). Migrations, `__init__`,
     and main.py are excluded. A module invoked only by a shell script is
     STILL counted, with the invoking script printed beside it as
     `external_refs`, because "1,004 lines whose only caller is a shell script
     that cron does not run" is the finding, not the exoneration. Read the
     external_refs before deleting anything: they are the dynamic-dispatch
     blind spot this check would otherwise have.

  4. uncalled_api_areas -- a first path segment of the served OpenAPI document
     (regenerated from THIS tree, never read from a committed artifact) that
     nothing in the repo calls: not frontend/src, not mobile/, not apps/, not
     scripts/, not another backend service. Grouped by area rather than by
     path because per-path matching cannot see `fetch(`${API}/detections`)`
     and produced 244 false positives when tried; the area is the granularity
     at which the real finding lives (`/leases`: mounted, typed, query keys
     defined, zero callers of any kind). Areas that are legitimately not
     called from inside the repo -- inbound webhooks, liveness probes -- are
     declared with a reason in scripts/dead_surface_rules.json.

WHAT THIS IS NOT. It is not a dead-code detector for functions, exports or
branches. It measures whole modules, whole routes and whole API areas, because
that is the unit a design generation is retired in.

Usage:
    python3 scripts/check_dead_surface.py
    python3 scripts/check_dead_surface.py --list
    python3 scripts/check_dead_surface.py --json
    python3 scripts/check_dead_surface.py --since HEAD
    python3 scripts/check_dead_surface.py --write-baseline --because "<reason>"
"""
from __future__ import annotations

import argparse
import ast
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

_WORKSHOP = Path(__file__).resolve().parents[1]
_BASELINE_PATH = Path(__file__).resolve().parent / "dead_surface_baseline.json"
_RULES_PATH = Path(__file__).resolve().parent / "dead_surface_rules.json"

COUNTS = (
    "unreferenced_frontend_modules",
    "top_level_routes_absent_from_nav",
    "unimported_backend_modules",
    "uncalled_api_areas",
)


def _rules() -> dict:
    try:
        return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tracked_files(tree: Path) -> list[Path]:
    """Every file this repo TRACKS, as absolute paths under `tree`.

    `git ls-files` rather than a filesystem walk, and the difference is not
    cosmetic: one app's directory holds 30,151 files, almost all of them
    gitignored recordings, and walking it turned a two-second scan into
    minutes. A git-archive extract (--since mode) has no .git and contains
    only tracked files already, so a walk of THAT is the same answer.

    `--others --exclude-standard` is not optional: a module a worker has
    written and not yet committed is still that worker's new dead surface, and
    a gate that only sees committed files would let every new one through on
    the run that introduced it.
    """
    files: list[Path] = []
    if (tree / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others",
                 "--exclude-standard"],
                cwd=tree, capture_output=True, check=True).stdout
            files = [tree / n.decode() for n in out.split(b"\0") if n]
        except (subprocess.SubprocessError, OSError, UnicodeDecodeError):
            files = []
    if not files:
        files = [p for p in tree.rglob("*")
                 if p.is_file() and "node_modules" not in p.as_posix()]
    return files + _nested_repo_files(tree)


_NESTED_REPOS = ("apps",)


def _nested_repo_files(tree: Path) -> list[Path]:
    """Tracked files of the nested repositories that hold installed content.

    THE CORPUS IS WHAT MAKES THIS CHECK MEAN ANYTHING. Every question here is
    "does anything reach this?", and a caller the scan cannot see is a caller
    that does not count. On 2026-09-04 the operator's apps moved into `apps/`,
    which is its OWN git repository and therefore invisible to the platform's
    `git ls-files` -- so a platform module or API area whose only callers were
    app code became unreachable in one step, having lost no caller at all.

    Their own `git ls-files` is used for the same reason the platform's is: one
    app's directory holds 30,151 files, almost all gitignored recordings, and
    walking it turns a two-second scan into minutes.

    A fresh clone has no `apps/`, adds nothing, and is unaffected.
    """
    out: list[Path] = []
    for name in _NESTED_REPOS:
        repo = tree / name
        if not (repo / ".git").exists():
            continue
        try:
            res = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others",
                 "--exclude-standard"],
                cwd=repo, capture_output=True, check=True).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        out.extend(repo / n.decode() for n in res.split(b"\0") if n)
    return out


# --------------------------------------------------------------------------
# 1. Unreferenced frontend modules (knip)
# --------------------------------------------------------------------------

def _knip(tree: Path) -> tuple[list[str] | None, str]:
    """knip's unused-file list for `tree`, or (None, reason).

    Run from the repo's own node_modules. `tree` is normally the working tree;
    in --since mode it is a git-archive extract, which has no node_modules of
    its own, so the live ones are linked in. Without that, every workspace
    import resolves to nothing and knip reports the whole kit as unused.
    """
    binary = _WORKSHOP / "node_modules" / ".bin" / "knip"
    if not binary.exists():
        return None, ("knip is not installed. `npm install` at the repo root, "
                      "or `npm install --save-dev knip@5`.")
    if not (tree / "knip.json").exists():
        return None, f"no knip.json at {tree}"
    linked: list[Path] = []
    if tree != _WORKSHOP:
        for rel in ("node_modules", "frontend/node_modules",
                    "packages/ui/node_modules"):
            src = _WORKSHOP / rel
            dst = tree / rel
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src, target_is_directory=True)
                linked.append(dst)
    try:
        res = subprocess.run(
            [str(binary), "--include", "files", "--no-config-hints",
             "--reporter", "json"],
            cwd=tree, capture_output=True, text=True, timeout=600,
        )
        # knip exits nonzero when it FINDS something, which is its normal
        # state here. Only an empty/unparseable stdout is a real failure.
        out = (res.stdout or "").strip()
        if not out:
            tail = (res.stderr or "").strip().splitlines()[-10:]
            return None, "knip produced no output:\n    " + "\n    ".join(tail)
        files = sorted(json.loads(out).get("files", []))
        return [f for f in files if f not in _referenced_by_an_app(tree)], ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"could not run knip: {exc}"
    finally:
        for dst in linked:
            dst.unlink(missing_ok=True)


_APP_SHELL_IMPORT_RE = re.compile(r"""["']@shell/([^"']+)["']""")


@functools.lru_cache(maxsize=8)
def _referenced_by_an_app(tree: Path) -> frozenset[str]:
    """Platform frontend modules that an INSTALLED APP imports.

    knip only sees npm workspaces, and `apps/` is not one -- it is a separate
    repository the platform deliberately does not track. So on 2026-09-04, when
    every app's frontend moved there, every shell module whose only importers
    were app pages became "unreferenced" in one step: the app shell helpers, the
    chat docks. None of them died; their callers moved out of the room knip is
    standing in.

    This is the ONE thing that changed about reachability, so it is the one
    thing corrected here rather than by declaring five modules as
    deliberately-dead, which would have been false and would have hidden them
    the next time they really did die.

    An app reaches a shell module through exactly one specifier shape,
    `@shell/<path-under-frontend-src>` -- the named seam declared in
    frontend/vite.config.ts and frontend/tsconfig.json. Anything else is not a
    reference this function should invent.
    """
    apps = tree / "apps"
    if not apps.is_dir():
        return frozenset()          # a fresh clone has no apps; nothing to add
    out: set[str] = set()
    for ui in sorted(apps.glob("*/ui")):
        for f in ui.rglob("*"):
            if f.suffix not in (".ts", ".tsx", ".js", ".jsx"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for spec in _APP_SHELL_IMPORT_RE.findall(text):
                stem = f"frontend/src/{spec}"
                for cand in (stem, stem + ".ts", stem + ".tsx",
                             stem + "/index.ts", stem + "/index.tsx"):
                    if (tree / cand).exists():
                        out.add(cand)
    # TRANSITIVELY. A module an app imports pulls in what IT imports, and those
    # are just as reachable. Without this pass the seam module cleared and the
    # two modules it imports stayed "dead" -- reachability is a closure, and
    # correcting only the first hop moves the false positive one edge along
    # rather than removing it.
    return frozenset(_reachable_from(tree, out))


def _reachable_from(tree: Path, seeds: set[str]) -> set[str]:
    """`seeds` plus everything they reach through relative imports."""
    seen: set[str] = set()
    queue = list(seeds)
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        try:
            text = (tree / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        base = Path(rel).parent
        for spec in _TS_IMPORT_RE.findall(text):
            stem = os.path.normpath(str(base / spec))
            for cand in (stem, stem + ".ts", stem + ".tsx",
                         stem + "/index.ts", stem + "/index.tsx"):
                if (tree / cand).exists() and cand not in seen:
                    queue.append(cand)
                    break
        for spec in _APP_SHELL_IMPORT_RE.findall(text):
            stem = f"frontend/src/{spec}"
            for cand in (stem, stem + ".ts", stem + ".tsx",
                         stem + "/index.ts", stem + "/index.tsx"):
                if (tree / cand).exists() and cand not in seen:
                    queue.append(cand)
                    break
    return seen


# --------------------------------------------------------------------------
# 2. Top-level routes absent from the nav
# --------------------------------------------------------------------------

_ROUTE_RE = re.compile(
    r'<Route\s+[^>]*?path=(?:"([^"]*)"|\{`([^`]*)`\})[^>]*?>', re.S)
_ELEMENT_RE = re.compile(r'element=\{<\s*([A-Za-z_][\w.]*)')
_LINK_RE = re.compile(
    r'(?:\bto=|\bhref=|\bto:\s*|navigate\(|redirect\()\s*\{?\s*'
    r'(?:"([^"]*)"|\'([^\']*)\'|`([^`]*)`)')
_REDIRECT_ELEMENT_RE = re.compile(r"Navigate|Redirect")


def _brace_spans(text: str, opener: str) -> list[tuple[int, int]]:
    """Character spans of every `opener{ ... }` region, brace-matched."""
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        start = text.find(opener, i)
        if start < 0:
            return spans
        cursor = start + len(opener)
        depth = 1
        while cursor < len(text) and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        spans.append((start, cursor))
        i = cursor


def _frontend_files(tree: Path, tracked: list[Path]) -> list[Path]:
    src = tree / "frontend" / "src"
    return sorted(p for p in tracked
                  if p.suffix in (".ts", ".tsx") and src in p.parents)


_SHELL_ENTRY = "frontend/src/main.tsx"
_TS_IMPORT_RE = re.compile(r"""(?:from|import)\s*\(?\s*["']([./][^"']*)["']""")


def _shell_modules(tree: Path, tracked: set[str]) -> set[str]:
    """Files the Workshop shell's entry point actually reaches.

    Needed because this repo builds a SECOND application out of the same
    source: `pages/apps/birds/standalone/main.tsx` is its own vite entry with
    its own root-mounted router, so its ten `<Route path="/atlas">`-style
    declarations are not Workshop routes and must not be judged against
    Workshop's nav. Reachability from the shell entry is the general form of
    that distinction, and it costs one BFS over relative imports.
    """
    if _SHELL_ENTRY not in tracked:
        return tracked
    seen: set[str] = set()
    queue = [_SHELL_ENTRY]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        try:
            text = _read_tracked(tree / rel) or ""
        except OSError:
            continue
        base = Path(rel).parent
        for match in _TS_IMPORT_RE.finditer(text):
            target = (base / match.group(1)).resolve()
            try:
                stem = target.relative_to(Path(tree).resolve()).as_posix()
            except ValueError:
                stem = (base / match.group(1)).as_posix()
            for candidate in (stem, stem + ".ts", stem + ".tsx",
                              stem + "/index.ts", stem + "/index.tsx"):
                if candidate in tracked:
                    queue.append(candidate)
                    break
    return seen



def _read_tracked(path: Path) -> str | None:
    """A tracked file's text, or None when it is not on disk.

    `tracked` comes from the git INDEX, so between `rm` and `git add` the index
    still names a file the tree no longer has. That is a deletion in progress,
    which is precisely the state a worker is in when it deletes a surface, and
    it is the state this checker most needs to survive. It used to raise
    FileNotFoundError from whichever read site got there first and take the
    whole run down. A gate that crashes is a gate that is not running.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _routes_and_links(tree: Path, tracked: list[Path]) -> tuple[list[dict], dict[str, list[str]]]:
    routes: list[dict] = []
    links: dict[str, list[str]] = {}
    rels = {p.relative_to(tree).as_posix() for p in tracked}
    shell = _shell_modules(tree, rels)
    for path in _frontend_files(tree, tracked):
        # A TRACKED FILE THAT IS NOT ON DISK IS A DELETION IN PROGRESS, not an
        # error. `tracked` comes from the git index, so between `rm` and `git
        # add` the index still names a file the tree no longer has -- exactly
        # the state a worker is in when it deletes a surface, which is the state
        # this checker most needs to survive. It used to raise FileNotFoundError
        # and take the whole run down, and a gate that crashes is a gate that is
        # not running: on 2026-09-01 it crashed on an unstaged TicketTable.tsx
        # deletion and reported nothing at all about the rest of the tree.
        text = _read_tracked(path)
        if text is None:
            continue
        rel = path.relative_to(tree).as_posix()
        route_spans = [(m.start(), m.end()) for m in _ROUTE_RE.finditer(text)]
        for match in (_ROUTE_RE.finditer(text) if rel in shell else ()):
            pattern = match.group(1) if match.group(1) is not None else match.group(2)
            element = _ELEMENT_RE.search(text[match.start():match.end()])
            routes.append({
                "path": pattern,
                "element": element.group(1) if element else "?",
                "file": rel,
            })
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        # A back button is a RETURN edge, not a way in. Excluded, or every
        # detail page vouches for the list page nothing else points at.
        excluded = _brace_spans(text, "back={") + route_spans
        for match in _LINK_RE.finditer(text):
            if any(a <= match.start() < b for a, b in excluded):
                continue
            value = match.group(1) or match.group(2) or match.group(3) or ""
            if value.startswith("/"):
                key = re.sub(r"\$\{[^}]*\}", "*", value).split("?")[0].split("#")[0]
                links.setdefault(key, []).append(rel)
    return routes, links


def _nav_targets(tree: Path, tracked: list[Path]) -> set[str]:
    """Every path a navigation list points at.

    Read from the whole frontend rather than only from places.ts: an app
    contributes its own rows through the registry, and a nav list written in a
    new file is by definition a file nobody thought to add to a gate list.
    """
    targets: set[str] = set()
    nav_re = re.compile(r'\bto:\s*(?:"([^"]*)"|`([^`]*)`)')
    for path in _frontend_files(tree, tracked):
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        text = _read_tracked(path)
        if text is None:
            continue
        for match in nav_re.finditer(text):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            if value.startswith("/"):
                targets.add(re.sub(r"\$\{[^}]*\}", "*", value).split("?")[0])
    return targets


def _scan_routes(tree: Path, rules: dict, tracked: list[Path]) -> dict:
    routes, links = _routes_and_links(tree, tracked)
    nav = _nav_targets(tree, tracked)
    allowed = set((rules.get("routes_allowed_off_nav") or {}).keys())
    per_route: dict[str, dict] = {}
    for route in routes:
        pattern = route["path"]
        if pattern in ("*", "/") or pattern in allowed:
            continue
        segments = [s for s in pattern.strip("/").split("/") if s]
        if len(segments) != 1 or segments[0].startswith(":"):
            continue                      # not a top-level page
        if _REDIRECT_ELEMENT_RE.search(route["element"]):
            continue                      # a deliberate compatibility shim
        if pattern in nav:
            continue
        per_route[pattern] = {
            "element": route["element"],
            "file": route["file"],
            "inbound": sorted(set(links.get(pattern, []))),
        }
    return {"total_routes": len(routes), "per_route": per_route}


# --------------------------------------------------------------------------
# 3. Backend modules nothing imports
# --------------------------------------------------------------------------

def _backend_modules(tree: Path) -> dict[str, tuple[Path, bool]]:
    root = tree / "backend"
    mods: dict[str, tuple[Path, bool]] = {}
    if not root.exists():
        return mods
    for path in root.rglob("*.py"):
        blob = path.as_posix()
        if any(part in blob for part in
               ("/.venv/", "/scratch/", "__pycache__", "/migrations/")):
            continue
        parts = list(path.relative_to(root).parts)
        is_pkg = parts[-1] == "__init__.py"
        if is_pkg:
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        if not parts:
            continue
        mods[".".join(parts)] = (path, is_pkg)
    return mods


_GLOB_DISCOVERY_RE = re.compile(
    r"""(?P<const>[A-Z_]+)\s*=\s*["'](?P<suffix>[A-Za-z0-9_]+)["']""")


def _glob_discovered(mods: dict[str, tuple[Path, bool]]) -> set[str]:
    """Modules a platform file imports DYNAMICALLY, by globbing for a suffix.

    The blind spot this closes is real and was introduced deliberately.
    `services/app_registry.py` no longer enumerates apps; it scans its own
    directory for `*_app.py` and imports each by name, so an app joins the
    platform by existing as a file. To `ast`, nothing imports those modules at
    all -- there is no import statement naming any of them -- and every app
    declaration read as dead code the moment the enumeration was removed.

    The convention is READ FROM THE SOURCE, not written here: the scanning file
    declares its suffix in a constant and globs on it, and both must be present
    for anything to be exempted. If the scan is ever deleted, these modules go
    straight back to being reported, which is the behaviour that makes this
    safe to have.
    """
    out: set[str] = set()
    for name, (path, is_pkg) in mods.items():
        text = _read_tracked(path) or ""
        if ".glob(" not in text or "import_module(" not in text:
            continue
        pkg = name.rsplit(".", 1)[0] if "." in name and not is_pkg else (name if is_pkg else "")
        for m in _GLOB_DISCOVERY_RE.finditer(text):
            suffix = m.group("suffix")
            # The constant must actually be used in the glob pattern, so an
            # unrelated string constant in a scanning file exempts nothing.
            if f'f"*{{{m.group("const")}}}.py"' not in text:
                continue
            for other in mods:
                if not pkg or not other.startswith(f"{pkg}."):
                    continue
                if other.rsplit(".", 1)[-1].endswith(suffix):
                    out.add(other)
    return out


def _app_imported_names(tree: Path) -> set[str]:
    """Every module name an INSTALLED APP's Python imports, absolute only.

    An extracted app runs in the same interpreter as the platform and imports
    platform modules by their real names (`from services.app_spec import
    AppSpec`). It is not under `backend/`, so `_backend_modules` never sees it
    and its imports never counted -- and on 2026-09-04 a platform module whose
    only importers were app personas became "unimported" having lost nothing.

    Relative imports are deliberately NOT resolved: inside an app they resolve
    to the app's own modules, which this function is not asked about.
    """
    found: set[str] = set()
    for path in _nested_repo_files(tree):
        if path.suffix != ".py":
            continue
        try:
            parsed = ast.parse(_read_tracked(path) or "")
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                base = node.module or ""
                found.add(base)
                for alias in node.names:
                    found.add(f"{base}.{alias.name}" if base else alias.name)
    return found


def _imported_names(mods: dict[str, tuple[Path, bool]]) -> set[str]:
    """Every module name a NON-TEST backend file imports, relatives resolved."""
    found: set[str] = set()
    for name, (path, is_pkg) in mods.items():
        if name.startswith("tests.") or name == "tests":
            continue
        try:
            tree = ast.parse(_read_tracked(path) or "")
        except (OSError, SyntaxError, ValueError):
            continue
        if is_pkg:
            package = name
        else:
            package = name.rsplit(".", 1)[0] if "." in name else ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    up = package.split(".") if package else []
                    if node.level > 1:
                        up = up[:-(node.level - 1)] if node.level - 1 <= len(up) else []
                    base = ".".join([p for p in up if p]
                                    + ([node.module] if node.module else []))
                else:
                    base = node.module or ""
                found.add(base)
                for alias in node.names:
                    found.add(f"{base}.{alias.name}" if base else alias.name)
    return found


_EXTERNAL_DIRS = ("scripts", "infra", "apps")


def _external_corpus(tree: Path, tracked: list[Path]) -> list[tuple[str, str]]:
    """Every tracked non-Python file that could name a backend module.

    Read once per scan rather than once per module: the per-module version
    re-read scripts/ and apps/ for each of 570 modules.
    """
    corpus: list[tuple[str, str]] = []
    own = {_BASELINE_PATH.name, _RULES_PATH.name, Path(__file__).name}
    for path in tracked:
        if path.suffix in (".py", ".pyc") or path.name in own:
            continue
        rel = path.relative_to(tree).as_posix()
        top = rel.split("/")[0]
        if top not in _EXTERNAL_DIRS and "/" in rel:
            continue
        if "/" not in rel and not rel.endswith(".sh"):
            continue
        try:
            body = _read_tracked(path)
            if body is None:
                continue
            corpus.append((rel, body))
        except OSError:
            continue
    return corpus


def _external_refs(corpus: list[tuple[str, str]], module: str,
                   rel_path: str) -> list[str]:
    """Non-Python files that name this module: `-m pkg.mod`, or its file path.

    This is the dynamic-dispatch blind spot, printed rather than silently
    forgiven. A module reached only from here is still counted: the question
    the count answers is "what does the SERVER reach", and a shell script that
    no schedule runs is not an answer to it.
    """
    return sorted({name for name, text in corpus
                   if module in text or rel_path in text})


def _scan_backend(tree: Path, rules: dict, tracked: list[Path]) -> dict:
    mods = _backend_modules(tree)
    imported = (_imported_names(mods) | _glob_discovered(mods)
                | _app_imported_names(tree))
    allowed = set((rules.get("backend_modules_allowed") or {}).keys())
    corpus = _external_corpus(tree, tracked)
    per_module: dict[str, dict] = {}
    for name, (path, is_pkg) in sorted(mods.items()):
        if is_pkg or name.startswith("tests.") or name in ("main", "tests"):
            continue
        if name in imported or name in allowed:
            continue
        rel = path.relative_to(tree).as_posix()
        per_module[name] = {
            "file": rel,
            "lines": len((_read_tracked(path) or "").splitlines()),
            "external_refs": _external_refs(corpus, name, rel),
        }
    return {"modules_scanned": len(mods), "per_module": per_module}


# --------------------------------------------------------------------------
# 4. API areas nothing calls
# --------------------------------------------------------------------------

_CALLER_DIRS = ("frontend/src", "mobile", "apps", "scripts", "backend/services",
                "backend/routes", "backend/mcp", "infra")
_BACKTICK_PROSE_RE = re.compile(r"`[^`\n]*`")
# A route DECLARATION is not a call to one. `routes/git.py` declares
# `@router.post("/push")` under a `/git` prefix, which is the only reference to
# the characters `/push` anywhere outside routes/push.py -- so without this the
# uncalled `/push` API reads as called by the git router.
_ROUTE_DECL_RE = re.compile(r"@\w+\.(?:get|post|put|patch|delete|head|options)"
                            r"\([^)]*\)", re.S)


def _AREA_CALL_RE(area: str) -> re.Pattern:
    """A CALL to `/area`, not a mention of it.

    The boundary is what does the work. `services/leases.py` in a docstring
    contains the characters `/leases` and is not a caller; a bare substring
    test reported the leases API as called by nine files, every one of them
    prose. So the segment must end at a URL boundary -- another segment, a
    quote, a query string or whitespace -- and never at `.` or `)`.
    """
    return re.compile(r"/" + re.escape(area) + r"(?=[/\"'`?\s]|$)")


_CALLER_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".py", ".sh", ".mjs", ".json",
                    ".yml", ".yaml", ".swift", ".kt")


def _openapi(tree: Path) -> tuple[dict | None, str]:
    """Regenerate the served schema from `tree`.

    Regenerated rather than read from frontend/src/api/schema.gen.ts for the
    reason check_opaque_fields.py gives: a committed artifact passes for
    anyone who touched it for any reason, and the point is to say something
    true about THIS source tree.
    """
    generator = tree / "scripts" / "gen_openapi.py"
    if not generator.exists():
        return None, f"gen_openapi.py not found under {tree}"
    venv = _WORKSHOP / "backend" / ".venv" / "bin" / "python"
    python = str(venv) if venv.exists() else sys.executable
    tmp = Path(tempfile.mkdtemp(prefix="workshop-deadsurface-"))
    try:
        out = tmp / "openapi.json"
        res = subprocess.run(
            [python, str(generator), "--schema-out", str(out),
             "--schema-only", "--quiet"],
            cwd=tree, capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-12:]
            return None, (f"gen_openapi.py exited {res.returncode}:\n    "
                          + "\n    ".join(tail))
        return json.loads(out.read_text(encoding="utf-8")), ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return None, f"could not generate the schema: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _caller_corpus(tree: Path, tracked: list[Path],
                   skip: tuple[str, ...]) -> list[tuple[str, str]]:
    """(path, text) for every tracked file that could contain an API call."""
    corpus: list[tuple[str, str]] = []
    for path in tracked:
        if path.suffix not in _CALLER_SUFFIXES:
            continue
        rel = path.relative_to(tree).as_posix()
        if not any(rel.startswith(d + "/") for d in _CALLER_DIRS):
            continue
        if rel in skip:
            continue
        try:
            body = _read_tracked(path) or ""
        except OSError:
            continue
        if path.suffix in (".py", ".sh"):
            # Backticks are prose markup in Python and shell, never a string
            # delimiter, and this repo's docstrings cite route paths constantly.
            body = _BACKTICK_PROSE_RE.sub(" ", body)
        if rel.startswith("backend/routes/") or rel.startswith("backend/mcp/"):
            body = _ROUTE_DECL_RE.sub(" ", body)
        corpus.append((rel, body))
    return corpus


def _scan_api(tree: Path, rules: dict, tracked: list[Path]) -> dict:
    doc, error = _openapi(tree)
    if doc is None:
        return {"ok": False, "error": error, "per_area": {}}
    areas: dict[str, list[str]] = {}
    for path in doc.get("paths") or {}:
        head = path.strip("/").split("/")[0]
        if head:
            areas.setdefault(head, []).append(path)
    declared = rules.get("api_areas_not_called_from_the_repo") or {}
    # The generated client names every path by construction, so it is not a
    # caller. Neither is the route module that DEFINES an area: excluded by
    # path, per area, below.
    corpus = _caller_corpus(tree, tracked, ("frontend/src/api/schema.gen.ts",))
    per_area: dict[str, dict] = {}
    for area, paths in sorted(areas.items()):
        if area in declared:
            continue
        defining = f"backend/routes/{area.replace('-', '_')}.py"
        pattern = _AREA_CALL_RE(area)
        if any(rel != defining and pattern.search(text) for rel, text in corpus):
            continue
        per_area[area] = {"routes": sorted(paths)}
    return {"ok": True, "areas_scanned": len(areas), "per_area": per_area}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def scan(tree: Path, rules: dict, *, with_knip: bool = True) -> dict:
    result: dict = {}
    tracked = _tracked_files(tree)

    if with_knip:
        files, why = _knip(tree)
    else:
        files, why = None, "not measured in this mode"
    result["unreferenced_frontend_modules"] = (
        {"items": {f: {} for f in files}} if files is not None
        else {"items": {}, "unavailable": why})

    routes = _scan_routes(tree, rules, tracked)
    result["top_level_routes_absent_from_nav"] = {
        "items": routes["per_route"], "total_routes": routes["total_routes"]}

    backend = _scan_backend(tree, rules, tracked)
    result["unimported_backend_modules"] = {
        "items": backend["per_module"],
        "modules_scanned": backend["modules_scanned"]}

    api = _scan_api(tree, rules, tracked)
    result["uncalled_api_areas"] = (
        {"items": api["per_area"], "areas_scanned": api.get("areas_scanned", 0)}
        if api["ok"] else {"items": {}, "unavailable": api["error"]})

    result["totals"] = {name: len(result[name]["items"]) for name in COUNTS}
    result["unavailable"] = {name: result[name]["unavailable"]
                             for name in COUNTS if "unavailable" in result[name]}
    return result


def _items_of(container: dict, name: str) -> set[str]:
    """The item set for `name`, from either a scan result or a baseline file.

    The BASELINE stores each count as a flat `{item: {}}` map, deliberately:
    check_ratchets_not_loosened.py globs scripts/*baseline*.json and measures
    each top-level value with `len()`, so a nested `{"items": {...}}` wrapper
    would read as the constant 1 and the "may not be re-pinned upward" gate
    would never see this file move.
    """
    node = container.get(name)
    if not isinstance(node, dict):
        return set()
    if "items" in node and isinstance(node["items"], dict):
        return set(node["items"])
    return set(node)


def _new_items(current: dict, judge: dict) -> dict[str, list[str]]:
    """Items present now and absent from what we are judged against."""
    out: dict[str, list[str]] = {}
    for name in COUNTS:
        gained = sorted(_items_of(current, name) - _items_of(judge, name))
        if gained:
            out[name] = gained
    return out


def _baseline_payload(current: dict) -> dict:
    return {name: {key: {} for key in current[name]["items"]} for name in COUNTS}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print every dead item, not just the totals")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--because", default="",
                    help="required by --write-baseline when a count goes UP")
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: also scan REV's tree and fail only on an "
                         "item this run introduced. The pinned baseline is "
                         "still reported.")
    args = ap.parse_args()

    rules = _rules()
    current = scan(_WORKSHOP, rules)

    if current["unavailable"] and not args.write_baseline:
        # A gate that cannot see the violation is worse than no gate. Say so
        # loudly rather than reporting a clean zero for the count it lost.
        for name, why in current["unavailable"].items():
            print(f"check_dead_surface: {name} could not be measured: {why}",
                  file=sys.stderr)

    baseline_path = Path(args.baseline)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        baseline = {}

    if args.write_baseline:
        raised = [n for n in COUNTS
                  if len(current[n]["items"]) > len(_items_of(baseline, n))]
        if raised and not args.because:
            print("check_dead_surface: refusing to pin a HIGHER count without "
                  f"--because. Raised: {', '.join(raised)}.\n"
                  "    The counts may only go down. If a scan area genuinely "
                  "widened, say so.", file=sys.stderr)
            return 2
        payload = _baseline_payload(current)
        if args.because:
            payload["_because"] = args.because
        baseline_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"check_dead_surface: wrote baseline to {baseline_path}")
        for name in COUNTS:
            print(f"    {name}: {len(current[name]['items'])}")
        return 0

    since_result: dict | None = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, _WORKSHOP) as base_tree:
                # knip is measured against the pinned baseline in both modes:
                # an extract resolves `@workshop/ui` through the LIVE
                # node_modules, so its kit files read as unreferenced and the
                # delta would be noise. The per-item baseline already keeps
                # inherited debt from blaming this run.
                since_result = scan(base_tree, rules, with_knip=False)
                since_result["unreferenced_frontend_modules"] = {
                    "items": {k: {} for k in
                              _items_of(baseline, "unreferenced_frontend_modules")}}
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dead_surface: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    judge = since_result if since_result is not None else baseline
    against = (f"the base ref {args.since}" if since_result is not None
               else "the pinned baseline")
    gained = _new_items(current, judge) if judge else {}

    if args.json:
        payload = {"totals": current["totals"], "new": gained,
                   "unavailable": current["unavailable"],
                   **{n: current[n]["items"] for n in COUNTS}}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if gained else 0

    print("check_dead_surface: surface nothing reaches")
    for name in COUNTS:
        note = ""
        if "unavailable" in current[name]:
            note = "   NOT MEASURED: " + current[name]["unavailable"]
        print(f"    {len(current[name]['items']):4d}  {name}{note}")

    if args.list:
        for name in COUNTS:
            items = current[name]["items"]
            if not items:
                continue
            print(f"\n  {name}")
            for key, meta in sorted(items.items()):
                detail = ""
                if "lines" in meta:
                    detail = f"  ({meta['lines']} lines)"
                    if meta.get("external_refs"):
                        detail += "  reached only from " + ", ".join(
                            meta["external_refs"])
                elif "inbound" in meta:
                    detail = ("  inbound: " + ", ".join(meta["inbound"])
                              if meta["inbound"] else "  inbound: none")
                elif "routes" in meta:
                    detail = "  " + ", ".join(meta["routes"][:6])
                print(f"    {key}{detail}")

    if not judge:
        print(f"\ncheck_dead_surface: no baseline at {baseline_path}. "
              "Run with --write-baseline to pin one.")
        return 0

    if not gained:
        print(f"\ncheck_dead_surface: nothing new against {against}")
        return 1 if current["unavailable"] else 0

    print(f"\n=== {sum(len(v) for v in gained.values())} new dead item(s) "
          f"against {against}")
    for name, items in gained.items():
        for key in items:
            print(f"    {name}: {key}")
    print("\n    A change deletes the surface it replaces in the same commit")
    print("    that adds the new one. If this is the new surface, delete the")
    print("    old one here. If it is genuinely unreachable on purpose (a")
    print("    manual probe, an inbound webhook), declare it with a reason in")
    print("    scripts/dead_surface_rules.json.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
