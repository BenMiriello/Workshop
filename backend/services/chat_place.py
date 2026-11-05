"""Where a chat is working: the page in an app it last worked on, as that app
reports it (`chat.place` in the app's manifest). The chat's header shows it
and goes there, so the operator can drop in on work done out of view.

An app answers `GET <place with {thread}>` on its MCP service with
`{label, path, title?, at?}` (`path` a Workshop page, `at` epoch seconds of the
last call) or `{}` when the chat has not worked in it. Apps that do not answer
in time are left out rather than holding the header up.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import httpx

from services import app_chat, chat_apps, chat_log, chat_store

log = logging.getLogger(__name__)

_TIMEOUT_S = 2.0


def own_slug(thread_id: str) -> str | None:
    """The app whose own chat this is, from an `app:<slug>` scope."""
    key = str((chat_log.read_meta(thread_id) or {}).get("context_key") or "")
    return (key[4:] or None) if key.startswith("app:") else None


def _asks(thread_id: str) -> list[tuple[app_chat.ChatDeclaration, str]]:
    out = []
    access = chat_store.get_thread_app_access(thread_id)
    for grant in chat_apps.grants(access, own_slug(thread_id)):
        decl = grant.decl
        if not decl.place:
            continue
        port = app_chat.service_port(decl.project, decl.mcp_service or "")
        if port is not None:
            out.append((decl, f"http://127.0.0.1:{port}{decl.place.replace('{thread}', quote(thread_id))}"))
    return out


async def _ask(client: httpx.AsyncClient, decl: app_chat.ChatDeclaration, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=_TIMEOUT_S)
        body = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        log.debug("chat place: %s did not answer: %s", decl.app_slug, e)
        return None
    path = str(body.get("path") or "") if isinstance(body, dict) else ""
    if not path.startswith("/"):
        return None
    return {"app": decl.app_slug, "app_label": decl.label, "path": path,
            "label": str(body.get("label") or decl.label)[:80],
            "title": str(body.get("title") or "")[:200],
            "at": body.get("at") if isinstance(body.get("at"), (int, float)) else None}


async def places(thread_id: str) -> list[dict]:
    """Every app's place for this chat, the most recently worked first."""
    asks = await asyncio.to_thread(_asks, thread_id)
    if not asks:
        return []
    async with httpx.AsyncClient() as client:
        found = await asyncio.gather(*(_ask(client, d, u) for d, u in asks))
    return sorted((p for p in found if p), key=lambda p: -(p["at"] or 0))
