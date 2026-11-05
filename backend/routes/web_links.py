"""Where a chat's web links open (services/web_links.py): the apps that can
open them, and the operator's choice."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import web_links

router = APIRouter(prefix="/web-links", tags=["chat"])


class WebLinkHandler(BaseModel):
    app: str
    label: str
    route: str
    # Shows web pages only inside the Workshop's native app (its page views);
    # anywhere else a link opens in a new window.
    native_pages: bool = False


class WebLinks(BaseModel):
    handlers: list[WebLinkHandler]
    chosen: WebLinkHandler | None = None


class WebLinkChoice(BaseModel):
    app: str | None = None


def _out(h: web_links.Handler | None) -> WebLinkHandler | None:
    return WebLinkHandler(app=h.app, label=h.label, route=h.route,
                          native_pages=h.native_pages) if h else None


@router.get("", response_model=WebLinks)
def get_web_links() -> WebLinks:
    return WebLinks(handlers=[_out(h) for h in web_links.handlers()], chosen=_out(web_links.chosen()))


@router.put("", response_model=WebLinks)
def put_web_links(body: WebLinkChoice) -> WebLinks:
    try:
        web_links.choose(body.app)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return get_web_links()
