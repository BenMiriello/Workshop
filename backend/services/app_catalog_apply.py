"""Installing catalog apps, turning them on and off, and applying defaults.

Installing clones the app's repository into the projects folder and registers
it with the record every install writes (`registry.register_installed`). An
app already in the projects folder under the same slug counts as installed:
it is never cloned over, and its record gains the catalog provenance.

On and off are the runner's start and stop, whose desired state the runner
already persists, so an app turned off stays off across restarts. Defaults
are applied once per app (`app_catalog.applied`): every `default` app is
installed and started the first time the catalog is read with it, and never
again by itself, so an app the operator turns off or removes stays off.
"""
from __future__ import annotations

import logging
import shutil
import threading

from routes import projects as projects_routes
from services import app_catalog, app_runner, installation, registry
from services.app_catalog import Entry

log = logging.getLogger("workshop.app_catalog")

__all__ = ["CatalogError", "apply_defaults", "apply_defaults_quietly", "find", "install",
           "listing", "pending_defaults", "set_on"]

_LOCK = threading.Lock()


class CatalogError(Exception):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _records() -> dict:
    return projects_routes._load_registry().get("projects") or {}


def _installed(slug: str, records: dict) -> bool:
    rec = records.get(slug)
    return isinstance(rec, dict) and rec.get("state") != "retired"


def _provenance(entry: Entry) -> dict:
    return {"from_catalog": {"catalog": installation.app_catalog_url(), "repo": entry.repo}}


def install(entry: Entry) -> dict:
    """Install one catalog app, or adopt the tree already there. Returns its
    projects record with its slug."""
    with _LOCK:
        data = projects_routes._load_registry()
        rec = (data.get("projects") or {}).get(entry.slug)
        if isinstance(rec, dict) and rec.get("state") != "retired":
            if not rec.get("from_catalog"):
                rec.update(_provenance(entry))
                projects_routes._save_registry(data)
            return {"slug": entry.slug, **rec}
        target = projects_routes._projects_root() / entry.slug
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                registry._run_git(["clone", entry.repo, str(target)], what="git clone (app)")
            except registry.RegistryError as e:
                shutil.rmtree(target, ignore_errors=True)
                raise CatalogError(e.message, status=502) from e
        return registry.register_installed(
            entry.slug, target, title=entry.name,
            purpose=entry.description or "Installed from the app catalog",
            provenance=_provenance(entry))


def find(slug: str) -> Entry:
    items, error = app_catalog.entries()
    entry = next((e for e in items if e.slug == slug), None)
    if entry is None:
        raise CatalogError(error or f"not in the app catalog: {slug}", status=404)
    return entry


def _start(slug: str) -> None:
    try:
        app_runner.start_app(slug)
    except app_runner.AppError as e:
        raise CatalogError(e.message, status=e.status) from e


def set_on(slug: str, on: bool) -> dict:
    """Turn a catalog app on (install if needed, then start) or off (stop).
    Either way its default counts as applied from then on."""
    entry = find(slug)
    if on:
        install(entry)
        _start(slug)
    elif _installed(slug, _records()):
        try:
            app_runner.stop_app(slug)
        except app_runner.AppError as e:
            raise CatalogError(e.message, status=e.status) from e
    app_catalog.mark_applied(slug)
    return _row(entry, _records())


def pending_defaults(items: list[Entry], done: set[str]) -> list[Entry]:
    """The default apps whose default has not been applied yet."""
    return [e for e in items if e.default and e.slug not in done]


def apply_defaults() -> dict:
    """Install and start every default app not applied yet. One that fails is
    left unapplied, so the next run tries it again."""
    items, error = app_catalog.entries(refresh=True)
    started: list[str] = []
    failed: dict[str, str] = {}
    for entry in pending_defaults(items, app_catalog.applied()):
        try:
            install(entry)
            _start(entry.slug)
        except CatalogError as e:
            failed[entry.slug] = e.message
            log.warning("default app %s not applied: %s", entry.slug, e.message)
            continue
        app_catalog.mark_applied(entry.slug)
        started.append(entry.slug)
    return {"started": started, "failed": failed, "error": error}


def apply_defaults_quietly() -> None:
    """`apply_defaults` for the boot: logs, never raises."""
    try:
        result = apply_defaults()
    except Exception as e:  # noqa: BLE001 - a catalog never blocks a boot
        log.warning("default apps skipped: %s", e)
        return
    if result["started"]:
        log.info("default apps installed and started: %s", ", ".join(result["started"]))
    if result["error"]:
        log.info("app catalog not fetched: %s", result["error"])


def _row(entry: Entry, records: dict) -> dict:
    installed = _installed(entry.slug, records)
    status = app_runner.app_status(entry.slug) if installed else {}
    return {
        **entry.as_dict(),
        "installed": installed,
        "on": installed and app_runner.wants_running(entry.slug),
        "running": status.get("phase") in ("up", "partial"),
    }


def listing(*, refresh: bool = False) -> dict:
    """The catalog's apps with whether each is installed, on and running."""
    items, error = app_catalog.entries(refresh=refresh)
    records = _records()
    return {"configured": bool(installation.app_catalog_url()),
            "apps": [_row(e, records) for e in items], "error": error}
