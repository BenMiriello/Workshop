"""What each installed app contributes to the shell's UI, read from its manifest.

THE HOLE THIS CLOSES. An app's backend reaches the platform through a
`workshop.apps` entry point, so installing one is `pip install` and a line in
the installed-apps registry. Its FRONTEND had no such seam: the shell found an
app by scanning for `apps/<slug>/ui/module.tsx` with `import.meta.glob`, which
Vite resolves AT BUILD TIME. So an app could be installed and removed at
runtime on the backend and not on the frontend, and every install ended in
"rebuild the shell". This module is the frontend's half of that seam.

DECLARE, THEN ACTIVATE. Everything the shell needs to DRAW an app -- its
launcher card, its dock tile, its breadcrumbs, its icon, its switcher group, its
legacy redirects, its status line -- is data in the app's own
`workshop.app.json`, and is served here without executing a line of app code.
The app's built ESM bundle is fetched only when the user first opens one of the
paths it declared.

THIS MODULE NAMES NO APP, and cannot: the roster comes from
`installed_apps.installed_app_ids()`, the directory from
`scope_tree.scope_root("app:<id>")`, and every field from the app's own manifest.

ONE MANIFEST READER. `app_runner._read_manifest_file` owns the filename set and
the jsonc comment tolerance and is already reused by `services/app_files.py`;
it is reused here too. There is no second manifest parser in this seam.

TOLERANT LIKE `manifests._normalize_entry`. A malformed page entry costs itself
and nothing else, and an app whose declaration cannot be honoured is returned
WITH an `error` rather than dropped -- a missing app looks exactly like an app
that was never installed, which is the failure mode that took the whole app
surface down on 2026-09-04 without logging anything.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from services import installed_apps, scope_tree
from wsplatform import paths

log = logging.getLogger("workshop.app_ui")

# The shape in `frontend/src/apps/contract.ts`: one default export carrying
# `{contract, exports}`, components that take no props, routes declared as data,
# and the `@workshop/app-host` context. Bump ONLY when that shape changes.
#
# React's own major is folded into this deliberately. React is external to an
# app's bundle, so an app compiled against 18 would silently receive 19 the day
# the shell moves; bumping React majors bumps this, which turns a silent runtime
# hazard into a refusal with a sentence.
CONTRACT = 1

# The mount points the platform publishes. A closed vocabulary is what keeps the
# shell from ever reading an app's id to decide anything: it reads `mount`.
MOUNTS = ("app.route", "app.layout", "app.settings", "app.session-provider")

# Served asset types. A module fetched with the wrong MIME type fails in a way
# that reads like a corrupt bundle, so the set is closed rather than guessed
# with `mimetypes`.
CONTENT_TYPES = {".js": "text/javascript", ".css": "text/css"}


class AppUiError(Exception):
    """A caller-facing refusal. The route maps `.status` to HTTP."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# The versions the SHELL actually serves
# --------------------------------------------------------------------------- #

def _build_info() -> dict[str, Any]:
    """`frontend/dist/build-info.json`, which `scripts/stamp_build.sh` writes.

    Deliberately not `node_modules`: the question is what the bundle being
    SERVED was compiled against, and the answer has to survive someone
    installing a different kit version afterwards.
    """
    p = paths.workshop_root() / "frontend" / "dist" / "build-info.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _kit_version_from_source() -> str:
    """The kit version in the tree. The fallback for an instance whose bundle
    was stamped before the stamp carried it, and for a dev origin."""
    p = paths.workshop_root() / "packages" / "ui" / "package.json"
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def runtime() -> dict[str, Any]:
    """What the shell publishes, for an app author and for the version check."""
    info = _build_info()
    return {
        "contract": CONTRACT,
        "kit": str(info.get("kit") or _kit_version_from_source() or "unknown"),
        "react": str(info.get("react") or "unknown"),
        "router": str(info.get("router") or "unknown"),
        "source_id": info.get("source_id"),
    }


# --------------------------------------------------------------------------- #
# Semver, only as much as a declared range needs
# --------------------------------------------------------------------------- #

_RANGE = re.compile(r"^\s*(\^|~|>=|=)?\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def kit_satisfies(version: str, spec: str) -> bool:
    """Whether the shell's kit `version` satisfies an app's declared `spec`.

    Supports what a manifest actually writes: `*`/empty (anything), `^0.7`,
    `~0.7.2`, `>=0.7`, and an exact `0.7.9`. A range this cannot parse is
    treated as satisfied rather than as a refusal, because refusing to render an
    installed app over a string this function does not understand is the worse
    of the two failures.

    `^` is npm's rule including the 0.x special case: `^0.7` allows 0.7.x and
    NOT 0.8.0, which is why adding a kit export is a fleet-wide event and why
    the shell publishes host wiring outside the kit.
    """
    spec = (spec or "").strip()
    if not spec or spec == "*":
        return True
    m = _RANGE.match(spec)
    v = _RANGE.match(version or "")
    if not m or not v:
        return True
    op = m.group(1) or "="
    want = tuple(int(g) if g else 0 for g in m.group(2, 3, 4))
    have = tuple(int(g) if g else 0 for g in v.group(2, 3, 4))
    if op == "=":
        # An exact spec with fewer parts is a prefix match: "0.7" means 0.7.x.
        parts = sum(1 for g in m.group(2, 3, 4) if g)
        return have[:parts] == want[:parts]
    if op == ">=":
        return have >= want
    if op == "~":
        return have >= want and have[:2] == want[:2]
    # "^": the leftmost non-zero part may not change.
    if want[0] != 0:
        return have >= want and have[0] == want[0]
    if want[1] != 0:
        return have >= want and have[:2] == want[:2]
    return have >= want and have[:3] == want[:3]


# --------------------------------------------------------------------------- #
# Reading one app's declaration
# --------------------------------------------------------------------------- #

def app_dir(app_id: str) -> Path:
    """The app's own tree. `scope_root` is the SINGLE mapping every per-scope
    path derives from, so this is not a second place that joins `apps/`."""
    return scope_tree.scope_root(f"app:{app_id}")


def _read_ui_block(app_id: str) -> dict[str, Any] | None:
    from services import app_runner

    root = app_dir(app_id)
    if not root.is_dir():
        return None
    try:
        manifest = app_runner._read_manifest_file(root)
    except Exception as e:  # a malformed manifest is that app's problem alone
        log.info("app %r: manifest unreadable: %s", app_id, e)
        return None
    if not isinstance(manifest, dict):
        return None
    ui = manifest.get("ui")
    return ui if isinstance(ui, dict) else None


def _crumb(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("label"):
        return None
    out: dict[str, Any] = {"label": str(raw["label"])}
    if raw.get("parent"):
        out["parent"] = str(raw["parent"])
    return out


def _pages(raw: Any) -> list[dict[str, Any]]:
    """Normalize the declared pages, dropping only the entries that are unusable.

    An entry with no path, no export or an unknown mount cannot be routed; one
    bad entry must not cost an app its other screens.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        export = str(entry.get("export") or "").strip()
        mount = str(entry.get("mount") or "app.route").strip()
        if not path or not export or mount not in MOUNTS:
            continue
        page: dict[str, Any] = {"path": path, "mount": mount, "export": export}
        if entry.get("layout"):
            page["layout"] = str(entry["layout"])
        crumb = _crumb(entry.get("crumb"))
        if crumb:
            page["crumb"] = crumb
        out.append(page)
    return out


def _mounts(raw: Any, mount: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        export = str(entry.get("export") or "").strip()
        if not export:
            continue
        item: dict[str, Any] = {"export": export, "mount": str(entry.get("mount") or mount)}
        if entry.get("label"):
            item["label"] = str(entry["label"])
        out.append(item)
    return out


def _str_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and v}


def _digest(path: Path) -> str:
    """Eight hex characters of the file's own bytes.

    Server side rather than baked into a filename, so an app's build needs no
    content hashing, its manifest names stable files, and a rebuilt app is
    picked up with no manifest edit. It is also what makes the asset route
    honestly immutable.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


# API-root-relative, NOT browser-absolute. The browser reaches this API under a
# prefix the backend does not own (the edge mounts it at `/api`), so the client
# prefixes its own base. A URL with the prefix baked in here would be a second
# place that decides where the API lives.
ASSET_ROUTE = "/base-apps/ui"


def _urls(app_id: str, ui: dict[str, Any], root: Path) -> tuple[dict[str, Any], str | None]:
    """`{entry, styles, provider}` with content hashes, plus any refusal.

    The refusals are the two that are INVISIBLE LATER: a declared file that is
    not built (the app never activates and the reason is a 404 deep in a
    console), and a stylesheet in the build directory that the manifest does not
    declare (the shell links what the manifest names, so an undeclared sheet is
    emitted, served and never requested -- and the app renders unstyled on the
    built origin only).
    """
    dir_name = str(ui.get("dir") or "dist/ui")
    build_dir = (root / dir_name).resolve()
    urls: dict[str, Any] = {"entry": None, "styles": [], "provider": None}
    if not str(build_dir).startswith(str(root.resolve())):
        return urls, f"ui.dir {dir_name!r} points outside the app's own directory"
    if not build_dir.is_dir():
        return urls, (
            f"{dir_name} does not exist, so this app has no built UI to load. "
            "Build it with @workshop/app-build."
        )

    def _url(rel: str) -> str | None:
        f = (build_dir / rel).resolve()
        if not str(f).startswith(str(build_dir)) or not f.is_file():
            return None
        return f"{ASSET_ROUTE}/{app_id}/{rel}?v={_digest(f)}"

    entry = str(ui.get("entry") or "app.js")
    urls["entry"] = _url(entry)
    if urls["entry"] is None:
        return urls, f"{dir_name}/{entry} is declared as ui.entry and is not built"

    declared = [str(s) for s in ui.get("styles") or [] if isinstance(s, str)]
    for style in declared:
        u = _url(style)
        if u is None:
            return urls, f"{dir_name}/{style} is declared in ui.styles and is not built"
        urls["styles"].append(u)

    emitted = sorted(p.name for p in build_dir.glob("*.css"))
    undeclared = [name for name in emitted if name not in declared]
    if undeclared:
        return urls, (
            f"{dir_name} contains {', '.join(undeclared)} and ui.styles does not list "
            "it, so the shell will never link it and this app renders unstyled"
        )

    provider = ui.get("provider_entry")
    if provider:
        urls["provider"] = _url(str(provider))
        if urls["provider"] is None:
            return urls, f"{dir_name}/{provider} is declared as ui.provider_entry and is not built"
    return urls, None


def contribution(app_id: str, shell_kit: str) -> dict[str, Any] | None:
    """One app's UI contribution, or None if it declares no UI at all."""
    ui = _read_ui_block(app_id)
    if ui is None:
        return None

    out: dict[str, Any] = {
        "id": app_id,
        "contract": int(ui.get("contract") or 0),
        "kit": str(ui.get("kit") or "*"),
        "headless": bool(ui.get("headless")),
        "icon": str(ui["icon"]) if ui.get("icon") else None,
        "group": str(ui["group"]) if ui.get("group") else None,
        "order": int(ui["order"]) if isinstance(ui.get("order"), int) else None,
        "link": None,
        "pages": _pages(ui.get("pages")),
        "layouts": _mounts(ui.get("layouts"), "app.layout"),
        "settings": _mounts(ui.get("settings"), "app.settings"),
        "redirects": _str_map(ui.get("redirects")),
        "legacy_tabs": _str_map(ui.get("legacy_tabs")),
        "status_endpoint": None,
        "urls": {"entry": None, "styles": [], "provider": None},
        "error": None,
    }
    link = ui.get("link")
    if isinstance(link, dict) and link.get("label") and link.get("to"):
        out["link"] = {"label": str(link["label"]), "to": str(link["to"])}
    status = ui.get("status")
    if isinstance(status, dict) and status.get("endpoint"):
        out["status_endpoint"] = str(status["endpoint"])

    # REFUSED BEFORE ANY FETCH, both of them, because the point of a version
    # check that arrives after the bundle is loaded is nothing at all.
    if out["contract"] != CONTRACT:
        out["error"] = (
            f"this app declares UI contract {out['contract'] or 'none'} and this shell "
            f"implements {CONTRACT}. Rebuild it against the current @workshop/app-build."
        )
        return out
    if not kit_satisfies(shell_kit, out["kit"]):
        out["error"] = (
            f"this app was built against @workshop/ui {out['kit']} and this shell serves "
            f"{shell_kit}. Rebuild it against {shell_kit} and widen its declared range."
        )
        return out

    # A headless app contributes its identity and no screens. That is a normal
    # declaration, not a degraded one, so it carries no error and no urls.
    if out["headless"] and not out["pages"] and not out["settings"]:
        return out

    urls, error = _urls(app_id, ui, app_dir(app_id))
    out["urls"] = urls
    out["error"] = error
    return out


def contributions() -> dict[str, Any]:
    """Every installed app's UI contribution, in installed order.

    Empty on a fresh instance with nothing installed, which is the correct
    blank-container state and not an error.
    """
    info = runtime()
    apps: list[dict[str, Any]] = []
    for app_id in installed_apps.installed_app_ids():
        try:
            entry = contribution(app_id, str(info["kit"]))
        except Exception as e:  # one app's declaration must not sink the rest
            log.warning("app %r: could not read its UI declaration: %s", app_id, e)
            continue
        if entry is not None:
            apps.append(entry)
    return {"contract": CONTRACT, "kit": str(info["kit"]), "apps": apps}


# --------------------------------------------------------------------------- #
# Serving the bytes
# --------------------------------------------------------------------------- #

def asset(app_id: str, rel_path: str) -> tuple[Path, str]:
    """`(file, content_type)` for one file inside an installed app's build dir.

    CONTAINMENT IS THE WHOLE JOB HERE: `rel_path` is caller controlled, so the
    candidate is resolved and then compared against the resolved build
    directory, which is the same check `workshop_public_app.spa` makes for the
    same reason. Only an INSTALLED app is served: an app the operator removed
    must stop being reachable without waiting for anything to rebuild.
    """
    if not installed_apps.is_installed(app_id):
        raise AppUiError(f"{app_id} is not installed", status=404)
    ui = _read_ui_block(app_id)
    if ui is None:
        raise AppUiError(f"{app_id} declares no ui block", status=404)
    root = app_dir(app_id).resolve()
    build_dir = (root / str(ui.get("dir") or "dist/ui")).resolve()
    candidate = (build_dir / rel_path).resolve()
    if not candidate.is_relative_to(build_dir) or not candidate.is_file():
        raise AppUiError("no such app asset", status=404)
    ctype = CONTENT_TYPES.get(candidate.suffix)
    if ctype is None:
        raise AppUiError(
            f"{candidate.suffix or 'this file type'} is not served from an app's UI "
            "directory; only .js and .css are",
            status=404,
        )
    return candidate, ctype
