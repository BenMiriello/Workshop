"""The app catalog and the apps that open over any page.

Mounted BEFORE routes/apps.py, whose `/apps/{app_id}` would otherwise take
`/apps/catalog` and `/apps/overlays` as app ids.

    GET  /apps/catalog                  the catalog's apps, installed/on/running
    POST /apps/catalog/defaults         install and start default apps not applied yet
    POST /apps/catalog/{slug}/install   clone and register, or adopt the tree there
    PUT  /apps/catalog/{slug}           {"on": bool}: start or stop, and remember it
    GET  /apps/overlays                 running apps that declare an `overlay`

docs/dictation-and-clipboard.md ("Default apps") and
docs/declaring-an-app-surface.md ("Opening over any page: overlay").
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import app_catalog_apply, overlay_apps
from services.app_catalog_apply import CatalogError

router = APIRouter(prefix="/apps", tags=["apps"])


class CatalogApp(BaseModel):
    slug: str
    name: str
    repo: str
    description: str
    default: bool
    installed: bool
    on: bool
    running: bool


class CatalogList(BaseModel):
    configured: bool
    apps: list[CatalogApp]
    error: str | None = None


class CatalogInstalled(BaseModel):
    slug: str
    dir: str


class CatalogDefaults(BaseModel):
    started: list[str]
    failed: dict[str, str]
    error: str | None = None


class CatalogToggle(BaseModel):
    on: bool


class OverlayApp(BaseModel):
    slug: str
    title: str
    shortcut: str | None = None
    url: str
    # Workshop events the app subscribes to (its manifest's `events` block).
    events: list[str] = []


class OverlayList(BaseModel):
    overlays: list[OverlayApp]


def _http(e: CatalogError) -> HTTPException:
    return HTTPException(e.status, e.message)


@router.get("/catalog", response_model=CatalogList)
def list_catalog(refresh: bool = False) -> dict:
    return app_catalog_apply.listing(refresh=refresh)


@router.post("/catalog/defaults", response_model=CatalogDefaults)
def apply_defaults() -> dict:
    return app_catalog_apply.apply_defaults()


@router.post("/catalog/{slug}/install", response_model=CatalogInstalled)
def install_app(slug: str) -> dict:
    try:
        rec = app_catalog_apply.install(app_catalog_apply.find(slug))
    except CatalogError as e:
        raise _http(e) from e
    return {"slug": rec["slug"], "dir": str(rec.get("dir") or "")}


@router.put("/catalog/{slug}", response_model=CatalogApp)
def toggle_app(slug: str, body: CatalogToggle) -> dict:
    try:
        return app_catalog_apply.set_on(slug, body.on)
    except CatalogError as e:
        raise _http(e) from e


@router.get("/overlays", response_model=OverlayList)
def list_overlays() -> dict:
    return {"overlays": overlay_apps.running_overlays()}
