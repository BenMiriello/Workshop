"""The page beside a chat, and what the chat may reach (services/page_link,
services/chat_apps).

    GET  /page-link/{thread}            level, follow, whether a page is beside it
    PUT  /page-link/{thread}            {level?, view?, link?}: a new level, how its
                                        work is seen (follow/show/hide), and/or which drawer
                                        the conversation is open in
    GET  /page-link/links/{link}/stream SSE: tool calls for the drawer's page
    PUT  /page-link/links/{link}        {page}: what is beside the drawer now
    POST /page-link/links/{link}/results/{call}   what running one returned

A drawer holds its stream under its own LINK id from the moment it opens, not
under a conversation, so a blank chat can be bound to it in the request that
comes before its first turn.

    GET  /chat-apps                     every app a chat could be given
    GET  /chat-apps/{thread}            this chat's grants
    PUT  /chat-apps/{thread}            replace them

`call_router` is the one the workshop tool server reaches, from loopback, with
the per-chat token it was handed at turn start:

    POST /page-link/call                {tool, args} -> {ok, text, image?} | {ok: False, error}
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import app_events, build_streaming, chat_apps, chat_log, chat_store, ownership, page_link

router = APIRouter(tags=["page-link"])
call_router = APIRouter(tags=["page-link"])

_KEEPALIVE_S = 20.0


class PageTool(BaseModel):
    name: str
    description: str = ""
    acts: bool = True


class PageState(BaseModel):
    level: str
    follow: bool = True
    view: str = "follow"
    online: bool
    app: str | None = None
    label: str = ""
    title: str = ""
    tools: list[PageTool] = []


class AccessOption(BaseModel):
    id: str
    label: str
    default: bool = True


class ChatApp(BaseModel):
    slug: str
    label: str
    about: str = ""
    access: list[AccessOption] = []
    follow: bool = False


class AppGrants(BaseModel):
    access: dict[str, list[str]]


class CallAnswer(BaseModel):
    ok: bool
    text: str = ""
    image: str | None = None
    error: str | None = None


def _pin_owner(thread_id: str) -> None:
    """A loopback call has no signed-in user: pin the owner the chat's token
    was minted for, so its stores resolve."""
    owner = page_link.owner_of(thread_id)
    if owner is None:
        raise HTTPException(403, "no owner is known for this chat's token")
    ownership.set_current_owner(owner)


def _known(thread_id: str) -> None:
    if not chat_log.exists(thread_id):
        raise HTTPException(404, "thread not found")


def _state(thread_id: str) -> PageState:
    p = page_link.page(thread_id)
    return PageState(
        level=chat_store.get_thread_page_level(thread_id),
        follow=chat_store.get_thread_page_follow(thread_id),
        view=chat_store.get_thread_page_view(thread_id),
        online=p is not None,
        app=p.app if p else None, label=p.label if p else "", title=p.title if p else "",
        tools=[PageTool(name=t["name"], description=t["description"], acts=t["acts"])
               for t in (p.tools if p else [])],
    )


@router.get("/page-link/{thread_id}", response_model=PageState)
async def get_page_link(thread_id: str) -> PageState:
    _known(thread_id)
    return _state(thread_id)


def _valid_link(link_id: str) -> None:
    if not page_link.valid_link_id(link_id):
        raise HTTPException(422, "link must be 8 to 64 letters, digits or dashes")


@router.put("/page-link/{thread_id}", response_model=PageState)
async def put_page_link(thread_id: str, body: dict = Body(...)) -> PageState:
    """`{level?}` sets the access level; `{view}` how the operator sees the
    chat's work in an app (follow, show or hide; `{follow}` true or false is
    follow or show), told to the apps that subscribe to `chat_view`; `{link}`
    (a drawer's link id, or null) says which drawer the conversation is open
    in. Any may come alone."""
    _known(thread_id)
    view = body.get("view")
    if "follow" in body and view is None:
        if not isinstance(body["follow"], bool):
            raise HTTPException(422, "follow must be true or false")
        view = "follow" if body["follow"] else "show"
    if view is not None:
        if view not in page_link.VIEWS:
            raise HTTPException(422, f"view must be one of {', '.join(page_link.VIEWS)}")
        if view != chat_store.get_thread_page_view(thread_id):
            chat_store.set_thread_page_view(thread_id, view)
            app_events.publish("chat_view", app_events.EVENTS["chat_view"](thread=thread_id, view=view))
    if "level" in body:
        if body["level"] not in page_link.LEVELS:
            raise HTTPException(422, f"level must be one of {', '.join(page_link.LEVELS)}")
        chat_store.set_thread_page_level(thread_id, body["level"])
    if "link" in body:
        link = body["link"]
        if link is not None:
            _valid_link(str(link))
        page_link.bind(thread_id, str(link) if link else None)
    return _state(thread_id)


class LinkPage(BaseModel):
    ok: bool


@router.put("/page-link/links/{link_id}", response_model=LinkPage)
async def put_link_page(link_id: str, body: dict = Body(...)) -> LinkPage:
    """`{page}`: what is beside this drawer now (an object, or null for none)."""
    _valid_link(link_id)
    page_link.set_page(link_id, page_link.parse_page(body.get("page")))
    return LinkPage(ok=True)


@router.get("/page-link/links/{link_id}/stream")
async def page_link_stream(link_id: str, request: Request):
    """Calls for the drawer's page to run, as `call` events. Holding this open
    is what makes the page reachable: when it closes, no conversation has a
    page beside it through this drawer any more."""
    _valid_link(link_id)
    q = page_link.attach(link_id)

    async def gen():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected() or build_streaming.SHUTTING_DOWN:
                    return
                try:
                    item = await asyncio.wait_for(q.get(), _KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: call\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            page_link.detach(link_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })


class Delivered(BaseModel):
    delivered: bool


@router.post("/page-link/links/{link_id}/results/{call_id}", response_model=Delivered)
async def post_result(link_id: str, call_id: str, body: dict = Body(...)) -> Delivered:
    """False when the call had already been answered or had timed out."""
    _valid_link(link_id)
    return Delivered(delivered=page_link.resolve(link_id, call_id, body))


@router.get("/chat-apps", response_model=list[ChatApp])
async def list_chat_apps() -> list[ChatApp]:
    return [ChatApp.model_validate(a) for a in await asyncio.to_thread(chat_apps.catalog)]


@router.get("/chat-apps/{thread_id}", response_model=AppGrants)
async def get_grants(thread_id: str) -> AppGrants:
    _known(thread_id)
    return AppGrants(access=chat_store.get_thread_app_access(thread_id))


@router.put("/chat-apps/{thread_id}", response_model=AppGrants)
async def put_grants(thread_id: str, body: AppGrants) -> AppGrants:
    _known(thread_id)
    try:
        access = await asyncio.to_thread(chat_apps.normalize, body.access)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    chat_store.set_thread_app_access(thread_id, access)
    return AppGrants(access=chat_store.get_thread_app_access(thread_id))


@call_router.post("/page-link/call", response_model=CallAnswer)
async def page_call(body: dict = Body(...),
                    x_page_token: str = Header(default="")) -> CallAnswer:
    """One page tool call from a turn's workshop tool server. The token names
    the chat; the level is read again here, so lowering it mid-turn stops the
    next call rather than the next turn."""
    thread_id = page_link.thread_for_token(x_page_token)
    if thread_id is None:
        raise HTTPException(403, "unknown page token")
    _pin_owner(thread_id)
    tool = str(body.get("tool") or "")
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    level = chat_store.get_thread_page_level(thread_id)
    unknown = page_link.unknown_tool(thread_id, tool, level)
    if unknown:
        return CallAnswer(ok=False, error=unknown)
    acts = page_link.tool_acts(thread_id, tool)
    if level not in page_link.READ_LEVELS:
        return CallAnswer(ok=False, error="The operator has set this chat to see only "
                          "what they attach, so the page cannot be read.")
    if acts and level not in page_link.ACT_LEVELS:
        return CallAnswer(ok=False, error="The operator lets this chat read the page "
                          "but not act on it.")
    # The page's own app hears which of its options this chat has on, so a
    # page tool reaching that data obeys them as the app's tool server does.
    p = page_link.page(thread_id)
    access = None
    if p is not None and p.app:
        access = await asyncio.to_thread(chat_apps.options_for, thread_id, p.app)
    answer = await page_link.call(thread_id, tool, args, access=access)
    return CallAnswer.model_validate(answer)


class AccessAnswer(BaseModel):
    text: str


def _describe_access(thread_id: str) -> str:
    level = chat_store.get_thread_page_level(thread_id)
    p = page_link.page(thread_id)
    lines = [f"Page level: {level}" + (f" (beside {p.label})" if p else " (no page is beside this chat now)")]
    access = chat_store.get_thread_app_access(thread_id)
    t = chat_store.get_thread(thread_id) or {}
    key = str(t.get("context_key") or "")
    own = key[4:] if key.startswith("app:") else None
    for app in chat_apps.catalog():
        slug = app["slug"]
        granted = slug == own or slug in access
        chosen = access.get(slug)
        if chosen is None and slug == own:
            chosen = [o["id"] for o in app["access"] if o.get("default", True)]
        opts = ", ".join(f"{o['id']} ({o['label']}) {'on' if o['id'] in (chosen or []) else 'off'}"
                         for o in app["access"]) or "no options"
        state = "this chat's own app" if slug == own else ("granted" if granted else "not granted")
        lines.append(f"- {slug} ({app['label']}): {state}; options: {opts}")
    return "\n".join(lines)


class OptionsAnswer(BaseModel):
    options: list[str] | None
    follow: bool = True
    view: str = "follow"


@call_router.get("/chat-access/{thread_id}/options/{app_slug}", response_model=OptionsAnswer)
async def chat_app_options(thread_id: str, app_slug: str,
                           x_workshop_options_token: str = Header(default="")) -> OptionsAnswer:
    """Which of an app's options this chat has on now (null for an app with
    none), and whether the app shows what the chat does in it (`follow`), for
    the app's own tool server during a turn (`page_link.options_headers`)."""
    if page_link.thread_for_options_token(x_workshop_options_token) != thread_id:
        raise HTTPException(403, "this token is not for that chat")
    _pin_owner(thread_id)
    return OptionsAnswer(options=await asyncio.to_thread(chat_apps.options_for, thread_id, app_slug),
                         follow=chat_store.get_thread_page_follow(thread_id),
                         view=chat_store.get_thread_page_view(thread_id))


@call_router.post("/chat-access/{thread_id}", response_model=AccessAnswer)
async def chat_access(thread_id: str, body: dict = Body(...),
                      x_page_token: str = Header(default="")) -> AccessAnswer:
    """`chat_access` from a turn's workshop tool server: read, and change, what
    this chat may reach. Loopback only, with the chat's own token: a chat
    changes its own settings and no other's."""
    if page_link.thread_for_token(x_page_token) != thread_id:
        raise HTTPException(403, "this token is not for that chat")
    _pin_owner(thread_id)
    _known(thread_id)
    level = body.get("level")
    if level is not None:
        if level not in page_link.LEVELS:
            raise HTTPException(422, f"level must be one of {', '.join(page_link.LEVELS)}")
        chat_store.set_thread_page_level(thread_id, level)
    grant = body.get("grant") or {}
    revoke = body.get("revoke") or []
    if grant or revoke:
        if not isinstance(grant, dict) or not isinstance(revoke, list):
            raise HTTPException(422, "grant is an object of app slug to option ids; revoke a list of slugs")
        access = dict(chat_store.get_thread_app_access(thread_id))
        access.update(grant)
        for slug in revoke:
            access.pop(str(slug), None)
        try:
            access = await asyncio.to_thread(chat_apps.normalize, access)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        unknown = [s for s in grant if s not in access]
        if unknown:
            raise HTTPException(422, f"no app offers tools as {', '.join(unknown)}")
        chat_store.set_thread_app_access(thread_id, access)
    text = await asyncio.to_thread(_describe_access, thread_id)
    return AccessAnswer(text=text)
