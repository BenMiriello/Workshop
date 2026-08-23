#!/usr/bin/env python3
"""Resolve how one app tree is previewed: origin, auth, viewports, driver.

This is the CONFIGURATION half of the preview capability. The verb half lives in
scripts/shoot.mjs and knows only how to load a URL at a viewport, screenshot it,
and report what the page complained about. Nothing in that file may name a port,
a cookie, a user or a browser version; everything of that kind is resolved here
and handed over as JSON.

Resolution order, most specific first:

  1. a `preview` block in the app's manifest (workshop.app.json / .jsonc), read
     through app_runner._read_manifest_file, which is the ONE reader of that file
     and already owns the filename set and the jsonc tolerance.
  2. a `workshop.preview.json` beside it. This exists because the platform's own
     source tree is not started by app_runner (systemd owns its services), so it
     has no service manifest, and inventing one purely to carry a preview block
     would change how resolve_manifest treats the self project. An app that has a
     manifest should declare `preview` there.
  3. what the detected stack implies: _STACK_DEFAULTS below, one row per stack,
     with the reason beside it. Adding a stack is a row.

Every string value may reference the environment as ${VAR} or ${VAR:-fallback},
so a machine's own origin stays in ~/.config/workshop/instance.env rather than in
a tracked file.

Usage (prints normalized JSON on stdout):

    python3 scripts/preview_config.py [app_dir]
    python3 scripts/preview_config.py --explain      # the table, with reasons
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

PREVIEW_FILE = "workshop.preview.json"

# Defaults that hold for every stack, because they are properties of REVIEWING a
# web page rather than of the stack serving it. A row below may override any of
# them, and an app's own declaration overrides both.
BASE_DEFAULTS: dict = {
    # No auth. A headless client that gets refused lands on a login page, and a
    # login page is a legible screenshot; guessing at an auth scheme is not.
    "auth": {"mode": "none"},
    # Phone first: the operator reviews on a phone and that is where layout
    # breaks first. The second is the widest thing worth checking.
    "viewports": [[390, 800], [1440, 900]],
    # Emulate touch below this width. 900 is the breakpoint most responsive
    # layouts in this tree switch at; keep it in step with the app's own or the
    # shot lies about which layout the operator sees.
    "touch_below": 900,
    # 2x, so text in a screenshot is legible when a person zooms into it.
    "device_scale_factor": 2,
    # Grace after networkidle for animations and late paints to settle.
    "settle_ms": 1200,
    # A cold dev server can take a while to compile the first route.
    "timeout_ms": 45000,
    # Empty version means "the bootstrap layer picks one it can run"; see the
    # Node version traps in scripts/shoot.sh.
    "driver": {"name": "playwright-core", "browser": "chromium", "version": ""},
}

# One row per stack. `detect` gets the app dir and answers yes or no; the first
# row that answers wins. `defaults` is merged over BASE_DEFAULTS. `why` is not
# decoration: it is what a person reads when a default is wrong for their app.
_STACK_DEFAULTS: list[dict] = [
    {
        "stack": "vite",
        "detect": lambda d: _pkg_mentions(d, ("vite",)),
        "defaults": {"origin": "http://127.0.0.1:5173"},
        "why": "Vite's own default dev port. An app the platform serves through "
               "its edge instead should declare the edge origin.",
    },
    {
        "stack": "next",
        "detect": lambda d: _pkg_mentions(d, ("next",)),
        "defaults": {"origin": "http://127.0.0.1:3000"},
        "why": "Next's default dev port.",
    },
    {
        "stack": "node",
        "detect": lambda d: (d / "package.json").is_file(),
        "defaults": {"origin": "http://127.0.0.1:3000"},
        "why": "Node web servers overwhelmingly default to 3000, so it is the "
               "least surprising guess for a package.json with no framework we "
               "recognize.",
    },
    {
        "stack": "python-asgi",
        "detect": lambda d: _text_mentions(
            d, ("requirements.txt", "pyproject.toml"), ("uvicorn", "fastapi", "starlette")
        ),
        "defaults": {"origin": "http://127.0.0.1:8000"},
        "why": "uvicorn's default port, which is what these apps are started on "
               "when nobody passes one.",
    },
    {
        "stack": "static",
        "detect": lambda d: (d / "index.html").is_file(),
        "defaults": {"origin": "http://127.0.0.1:8080"},
        "why": "A plain directory of files has no server of its own, so this is "
               "the port of whatever static server is pointed at it.",
    },
]

# Last resort when no row matches: previewing needs SOME origin, and refusing
# with a clear line beats screenshotting a guess.
_NO_STACK = {
    "stack": "unknown",
    "why": "Nothing about the tree said how it is served. Declare `preview.origin` "
           "in workshop.app.json (or " + PREVIEW_FILE + ").",
}


def _pkg_mentions(app_dir: Path, names: tuple[str, ...]) -> bool:
    """True if package.json declares any of `names` as a dependency."""
    p = app_dir / "package.json"
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.update(block)
    return any(n in deps for n in names)


def _text_mentions(app_dir: Path, files: tuple[str, ...], needles: tuple[str, ...]) -> bool:
    for name in files:
        p = app_dir / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8").lower()
        except OSError:
            continue
        if any(n in text for n in needles):
            return True
    return False


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value):
    """Expand ${VAR} and ${VAR:-fallback} in every string, recursively."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _read_declared(app_dir: Path) -> tuple[dict, str]:
    """The app's own preview declaration, and where it came from."""
    try:
        from services.app_runner import _read_manifest_file  # the one manifest reader
    except ImportError:
        # A backend that will not import must not make the tool unusable; the
        # sidecar file and the stack defaults still work.
        _read_manifest_file = None  # type: ignore[assignment]
    raw = None
    if _read_manifest_file is not None:
        try:
            raw = _read_manifest_file(app_dir)
        except Exception as e:  # AppError: the manifest exists and is malformed
            raise SystemExit(f"preview: {e}")
    if isinstance(raw, dict) and isinstance(raw.get("preview"), dict):
        return raw["preview"], "workshop.app.json preview block"

    p = app_dir / PREVIEW_FILE
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"preview: {p} is malformed: {e}")
        if isinstance(data, dict):
            block = data.get("preview") if isinstance(data.get("preview"), dict) else data
            return block, PREVIEW_FILE
    return {}, ""


def _detect_stack(app_dir: Path) -> dict:
    for row in _STACK_DEFAULTS:
        try:
            if row["detect"](app_dir):
                return row
        except OSError:
            continue
    return _NO_STACK


def _normalize_auth(raw) -> dict:
    """Coerce an auth declaration into one of the four shapes the runner knows.

    Tolerant by design, like services/manifests.py _normalize_entry: an entry we
    cannot make sense of becomes `none` rather than an error, because a shot of a
    login page is more useful than no shot.
    """
    if not isinstance(raw, dict):
        return {"mode": "none"}
    mode = str(raw.get("mode") or "none").strip().lower()
    if mode == "cookie":
        return {
            "mode": "cookie",
            "name": str(raw.get("name") or "session"),
            "value": str(raw.get("value") or ""),
            # A shell command that prints the cookie value. Run by the caller
            # (shoot.sh) with the app dir as cwd, and cached; the resolver never
            # executes anything.
            "token_command": str(raw.get("token_command") or ""),
        }
    if mode == "header":
        return {
            "mode": "header",
            "name": str(raw.get("name") or "Authorization"),
            "value": str(raw.get("value") or ""),
            "value_command": str(raw.get("value_command") or ""),
        }
    if mode == "basic":
        return {
            "mode": "basic",
            "username": str(raw.get("username") or ""),
            "password": str(raw.get("password") or ""),
        }
    return {"mode": "none"}


def _normalize_viewports(raw, fallback: list) -> list:
    out: list[list[int]] = []
    for entry in raw if isinstance(raw, list) else []:
        pair = None
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            pair = (entry[0], entry[1])
        elif isinstance(entry, dict) and entry.get("width") and entry.get("height"):
            pair = (entry["width"], entry["height"])
        if not pair:
            continue
        try:
            out.append([int(pair[0]), int(pair[1])])
        except (TypeError, ValueError):
            continue
    return out or [list(v) for v in fallback]


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def resolve(app_dir: Path) -> dict:
    """The normalized preview config for one app tree."""
    app_dir = app_dir.resolve()
    declared, source = _read_declared(app_dir)
    declared = _expand(declared if isinstance(declared, dict) else {})
    row = _detect_stack(app_dir)
    merged = dict(BASE_DEFAULTS)
    merged.update(row.get("defaults") or {})

    origin = str(declared.get("origin") or merged.get("origin") or "").rstrip("/")
    if not origin:
        raise SystemExit("preview: " + row["why"])

    driver_declared = declared.get("driver")
    driver = dict(BASE_DEFAULTS["driver"])
    if isinstance(driver_declared, dict):
        driver.update({k: str(v) for k, v in driver_declared.items() if isinstance(k, str)})
    elif isinstance(driver_declared, str):
        driver["name"] = driver_declared

    return {
        "app_dir": str(app_dir),
        "stack": row["stack"],
        "source": source or ("stack defaults (" + row["stack"] + ")"),
        "origin": origin,
        "auth": _normalize_auth(declared.get("auth", merged["auth"])),
        "viewports": _normalize_viewports(declared.get("viewports"), merged["viewports"]),
        "touch_below": _as_int(declared.get("touch_below"), merged["touch_below"]),
        "device_scale_factor": _as_int(
            declared.get("device_scale_factor"), merged["device_scale_factor"]
        ),
        "settle_ms": _as_int(declared.get("settle_ms"), merged["settle_ms"]),
        "timeout_ms": _as_int(declared.get("timeout_ms"), merged["timeout_ms"]),
        "driver": driver,
    }


def _explain() -> str:
    lines = ["stack defaults, in match order:", ""]
    for row in _STACK_DEFAULTS:
        origin = (row.get("defaults") or {}).get("origin", "")
        lines.append(f"  {row['stack']:<12} {origin}")
        lines.append(f"               {row['why']}")
    lines += ["", "everything else, for every stack:", ""]
    for key, value in BASE_DEFAULTS.items():
        lines.append(f"  {key:<20} {json.dumps(value)}")
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--explain"]
    if "--explain" in sys.argv[1:]:
        print(_explain())
        raise SystemExit(0)
    target = Path(args[0]) if args else REPO_ROOT
    if not target.is_dir():
        raise SystemExit(f"preview: {target} is not a directory")
    print(json.dumps(resolve(target), indent=2))
