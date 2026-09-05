#!/usr/bin/env python3
"""The two app-loader traps, asserted against real build output.

Both traps share one property: A DEVELOPER WHO DOES THE NORMAL THINGS SEES
NOTHING WRONG. So each gets an assertion that runs without anyone deciding to
look.

    --dist [DIR]      the SHELL's build. Exactly one React, one import map, and
                      every map value naming a file that is actually there.
    --app DIR         an APP's build. Exactly the stylesheets its manifest
                      declares, the entry its manifest names, and no React.
    both together     every bare specifier the app's bundle imports is a key in
                      the shell's map. That is the pairing the two halves make:
                      an externalized specifier with no map entry is a bare
                      specifier in the browser, which fails in a way that reads
                      like a loader bug and is not one.

TRAP A, two Reacts. The browser keys modules by URL. An import map value
pointing into `/node_modules/` (whose dev URL carries a `?v=` token that moves)
is a second URL, a second module instance and a second React, and every hook in
the app throws -- in dev only. The production half of the same trap is a build
that emits React into more than one chunk, which would mean the shared entry did
not share.

TRAP B, an unstyled app. Vite's `cssCodeSplit` default emits per-chunk
stylesheets whose `<link>` is injected by a preload helper that only exists
inside the importing bundle's own rollup graph. An app fetched by URL gets no
helper: the stylesheet is emitted, is served, and is never requested. In dev
there is no chunk CSS at all, so the app looks right on the dev origin, right in
the tests, right in typecheck, and wrong on the built origin -- which is the
only origin the operator uses.

NAMES NO APP: `--app` is given a directory and reads that app's own manifest.

Exit 0 on pass, 1 on a real failure, 2 when there is nothing to measure (no
build yet) -- which is deliberately NOT a pass.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# React's own implementation file, named in the license header it ships with.
# NOT `__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED`: that string is a
# property NAME that react-dom and the jsx runtime both reference, so it appears
# in three chunks of a perfectly correct build and would fail this check for the
# wrong reason (measured 2026-09-05).
REACT_IMPL = "react.production.min.js"
MANIFEST_NAMES = ("workshop.app.json", "workshop.app.jsonc")


def _read_manifest(app_dir: Path) -> dict:
    for name in MANIFEST_NAMES:
        p = app_dir / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        if name.endswith("jsonc"):
            text = "\n".join(
                ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))
        return json.loads(text)
    raise SystemExit(f"check_app_loader: {app_dir} has no workshop.app.json")


def import_map_of(dist: Path) -> dict[str, str] | None:
    index = dist / "index.html"
    if not index.is_file():
        return None
    maps = re.findall(r'<script type="importmap">(.*?)</script>',
                      index.read_text(encoding="utf-8"), re.S)
    if len(maps) != 1:
        return None
    try:
        return (json.loads(maps[0]) or {}).get("imports", {})
    except json.JSONDecodeError:
        return None


# `import ... from "x"` and bare `import "x"` in an emitted ESM bundle. The app
# build externalizes exactly the SHARED list, so anything matching here is
# something the browser will resolve through the map or fail on.
_BARE_IMPORT = re.compile(r"""\bimport\s*(?:[^"';]*?\bfrom\s*)?["']([^."'][^"']*)["']""")


def check_specifiers(app_entry: Path, imports: dict[str, str]) -> list[str]:
    source = app_entry.read_text(encoding="utf-8", errors="ignore")
    missing = sorted({
        spec for spec in _BARE_IMPORT.findall(source) if spec not in imports
    })
    if not missing:
        return []
    return [
        f"this app imports {', '.join(missing)} and the shell's import map does not "
        "publish it, so the browser cannot resolve it. Either add it to SHARED in "
        "@workshop/app-build (and a module beside frontend/src/runtime/shared) or stop "
        "externalizing it."
    ]


def check_dist(dist: Path) -> list[str]:
    failures: list[str] = []
    index = dist / "index.html"
    if not index.is_file():
        print(f"check_app_loader: no build at {dist} -- nothing to measure")
        raise SystemExit(2)

    assets = sorted((dist / "assets").glob("*.js"))
    with_react = [p for p in assets if REACT_IMPL in p.read_text(encoding="utf-8", errors="ignore")]
    if len(with_react) != 1:
        failures.append(
            f"{len(with_react)} chunk(s) carry React itself ({', '.join(p.name for p in with_react) or 'none'}). "
            "Exactly one means the shared entries share; two means production has two Reacts "
            "and every app bundle's hooks are a coin toss.")

    html = index.read_text(encoding="utf-8")
    maps = re.findall(r'<script type="importmap">(.*?)</script>', html, re.S)
    shared = [p for p in assets if p.name.startswith("shared-")]
    if not maps and not shared:
        # A build made before this stage existed has neither the map nor the
        # entries it names. That is not a regression, it is an old bundle, and
        # calling it a failure would make this command fail for everyone until
        # the next apply step. NOT a pass either: exit 2 says unmeasured.
        print(f"check_app_loader: the build at {dist} predates the runtime loader "
              "(no import map and no shared runtime entries). Run the apply step, "
              "then re-run this.")
        raise SystemExit(2)
    if len(maps) == 0:
        failures.append(
            "index.html carries no import map, and this build DOES emit the shared runtime "
            "entries, so an app bundle would fail on every bare specifier. The plugin ran "
            "and injected nothing.")
    elif len(maps) > 1:
        failures.append(
            f"index.html carries {len(maps)} import maps. Multiple maps in one document are "
            "not supported in Firefox and only the first is honoured elsewhere.")
    if len(maps) == 1:
        first_module = html.find('<script type="module"')
        if first_module != -1 and html.find('<script type="importmap">') > first_module:
            failures.append(
                "the import map comes after the first module script, so the imports that "
                "script starts are resolved without it.")
        imports = (json.loads(maps[0]) or {}).get("imports", {})
        if not imports:
            failures.append("the import map is empty, so an app bundle resolves nothing.")
        for spec, url in imports.items():
            if "/node_modules/" in url:
                failures.append(f"{spec} maps into /node_modules/ ({url}): trap A.")
            if "?v=" in url:
                failures.append(f"{spec} carries a re-optimization token ({url}): trap A.")
            target = dist / url.lstrip("/")
            if not target.is_file():
                failures.append(f"{spec} maps to {url}, which the build did not emit.")
    return failures


def check_app(app_dir: Path) -> list[str]:
    failures: list[str] = []
    ui = (_read_manifest(app_dir).get("ui") or {})
    if not isinstance(ui, dict):
        return [f"{app_dir} declares no ui block, so it contributes no UI at all."]
    if ui.get("headless"):
        print(f"check_app_loader: {app_dir.name} declares itself headless -- no UI to check")
        return []
    build = app_dir / str(ui.get("dir") or "dist/ui")
    if not build.is_dir():
        print(f"check_app_loader: {build} does not exist -- build the app first")
        raise SystemExit(2)

    entry = build / str(ui.get("entry") or "app.js")
    if not entry.is_file():
        failures.append(f"ui.entry names {entry.name} and the build did not emit it.")
    elif REACT_IMPL in entry.read_text(encoding="utf-8", errors="ignore"):
        failures.append(
            "this bundle contains React itself, so the document loads a second copy and "
            "every hook in this app throws. React must stay external.")

    declared = [str(s) for s in ui.get("styles") or []]
    emitted = sorted(p.name for p in build.glob("*.css"))
    if len(emitted) > 1:
        failures.append(
            f"{len(emitted)} stylesheets in {build.name} ({', '.join(emitted)}). The shell "
            "links an app's stylesheets by name and gets no preload helper, so a chunk "
            "stylesheet is emitted, served and never requested.")
    for name in emitted:
        if name not in declared:
            failures.append(
                f"{name} is emitted and ui.styles does not list it, so the shell never "
                "links it and this app renders unstyled in a build: trap B.")
    for name in declared:
        if not (build / name).is_file():
            failures.append(f"ui.styles declares {name} and the build did not emit it.")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", nargs="?", const=str(REPO / "frontend" / "dist"),
                    help="the shell's build directory (default frontend/dist)")
    ap.add_argument("--app", help="an app directory holding workshop.app.json")
    args = ap.parse_args()
    if not args.dist and not args.app:
        ap.error("one of --dist or --app is required")

    failures: list[str] = []
    if args.dist:
        failures += check_dist(Path(args.dist).resolve())
    if args.app:
        app_dir = Path(args.app).resolve()
        failures += check_app(app_dir)
        if args.dist:
            imports = import_map_of(Path(args.dist).resolve())
            ui = (_read_manifest(app_dir).get("ui") or {})
            entry = app_dir / str(ui.get("dir") or "dist/ui") / str(ui.get("entry") or "app.js")
            if imports is not None and entry.is_file():
                failures += check_specifiers(entry, imports)

    if failures:
        print("check_app_loader: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("check_app_loader: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
