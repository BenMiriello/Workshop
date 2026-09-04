"""Apps API (app-is-the-entity-2026-08-20.md, stage B).

  GET    /apps                    ?kind=&archived=      list
  POST   /apps                                          create
  GET    /apps/{id}                                      one app
  PATCH  /apps/{id}                                       rename / re-icon / archive
  DELETE /apps/{id}                                       soft delete
  GET    /apps/{id}/chats         ?archived=             real, from chat_thread.app_id
  GET    /apps/{id}/tickets                               real, from the JSON ticket store
  GET    /apps/{id}/notes                                 real, from services/knowledge.py
  GET    /apps/{id}/runs                                  real, from services/jobs.py
  GET    /apps/{id}/agents                                 real, roster agents linked to the app's project
  GET    /apps/{id}/files         ?path=                    tree listing of the app's linked project tree
  GET    /apps/{id}/files/content ?path=                    one file's content
  PUT    /apps/{id}/files/content                            write one file's content

`{id}` is the surrogate (a uuid), never the slug -- a rename is a PATCH, not a
migration (design doc sec 5.1). Every nested route 404s on an unknown id
rather than returning an empty list for one, so "this app has no tickets" and
"there is no such app" are never confused by the caller.

WHY SOME COLLECTIONS ARE HONESTLY EMPTY FOR MOST APPS TODAY. Only chats and
(nominally) tickets are backed by Postgres tables with a real `app_id` column
(stage A). Tickets, notes, runs and agents are served by resolving
the app's slug -- and, where it has one, its `project_slug` link
(`services/app_links.py`) -- against the file stores this installation
already has. An app with no linked project (an installed-only app: no repo,
no working tree) genuinely has no runs or tickets, and these routes
return `200` with an empty list for it, never a 404 and never another app's
rows. See `services/apps.py` for the full reasoning, collection by collection.

NOT the app's chat PERSONA. `services/app_persona.py` is explicit that an
app's in-chat assistant is not a roster agent; `GET /apps/{id}/agents`
answers a different question (which roster agents work ON this app,
mirroring the project build view) and the two are never merged into one list.

The gate is applied in main.py (operator only), matching every other
platform-control surface (`/jobs`, `/tasks`, `/notes`, `/projects`).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from routes.agents import RosterAgent
from routes.api_models import Job, Task
from routes.knowledge import NoteSummary
from services import app_files as app_files_svc
from services import apps as apps_svc

router = APIRouter(prefix="/apps", tags=["apps"])


# --- schemas ------------------------------------------------------------------

class App(BaseModel):
    id: str
    slug: str
    name: str
    icon: str | None = None
    kind: str
    dev_only: bool = False
    parent_id: str | None = None
    # The file-registry bridge (see db.models.App). None for an
    # installed-only app with no working tree of its own.
    project_slug: str | None = None
    archived_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AppCollection(BaseModel):
    apps: list[App]


class AppCreateIn(BaseModel):
    slug: str
    name: str
    icon: str | None = None
    kind: str = "app"
    parent_id: str | None = None
    # Left unset: resolved from services.app_links (the registry merge) at
    # creation time. Set explicitly only to override that resolution.
    project_slug: str | None = None


class AppPatchIn(BaseModel):
    name: str | None = None
    icon: str | None = None
    slug: str | None = None
    parent_id: str | None = None
    # Tri-state at the wire: absent (unset in the request body) means "leave
    # archived-ness alone"; `true`/`false` sets or clears `archived_at`.
    archived: bool | None = None


class AppDeleted(BaseModel):
    deleted: bool


class AppChat(BaseModel):
    """The SAME summary `GET /api/chat/threads` returns, narrowed to one app.

    The four fields below the timestamps were missing, so this endpoint could
    not describe a chat the way the global list does and the app's Chats tab
    rendered bare titles. A response model that does not declare a field drops
    it silently on the wire, which is how they went missing in the first
    place."""

    id: str
    context_key: str | None = None
    scope: str | None = None
    title: str
    mode: str
    model: str | None = None
    agent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None
    # How many turns, the preview line, when the operator last spoke, the
    # branch it is pinned to, and whether a turn is in flight right now.
    message_count: int = 0
    last_message: str = ""
    last_user_at: str | None = None
    branch_slug: str | None = None
    running: bool = False
    # ARMED AND ROLE, declared here for the reason the docstring above already
    # gives. `chats_for_app` builds its rows with the same builder the global
    # list uses, so both fields are in the dict; a response model that does not
    # declare a field drops it silently on the wire, which is exactly how the
    # five above went missing. The app's Chats tab and the Work tab's thread
    # list both read them (threads-branches-and-pages-2026-08-29.md section 1:
    # an "agent" is a thread that is armed).
    armed: str = "off"
    role: str | None = None


class AppChatList(BaseModel):
    chats: list[AppChat]


class AppTicketList(BaseModel):
    tickets: list[Task]


class AppNoteList(BaseModel):
    notes: list[NoteSummary]


class AppRunList(BaseModel):
    runs: list[Job]


class AppAgentList(BaseModel):
    agents: list[RosterAgent]


# --- helpers --------------------------------------------------------------

def _unavailable() -> HTTPException:
    return HTTPException(
        503, "the relational store is not configured or not reachable; "
             "apps are served from Postgres and have no file fallback")


# --- the app entity -----------------------------------------------------------

@router.get("", response_model=AppCollection, response_model_exclude_unset=True)
async def list_apps(kind: str | None = None, archived: bool | None = None) -> dict:
    try:
        return {"apps": apps_svc.list_apps(kind=kind, archived=archived)}
    except apps_svc.AppsUnavailable:
        raise _unavailable()


@router.post("", response_model=App, status_code=201, response_model_exclude_unset=True)
async def create_app(body: AppCreateIn) -> dict:
    try:
        return apps_svc.create_app(slug=body.slug, name=body.name, icon=body.icon,
                                   kind=body.kind, parent_id=body.parent_id,
                                   project_slug=body.project_slug)
    except apps_svc.AppsUnavailable:
        raise _unavailable()
    except apps_svc.SlugTaken as e:
        raise HTTPException(409, str(e))


@router.get("/{app_id}", response_model=App, response_model_exclude_unset=True)
async def get_app(app_id: str) -> dict:
    try:
        app = apps_svc.get_app(app_id)
    except apps_svc.AppsUnavailable:
        raise _unavailable()
    if app is None:
        raise HTTPException(404, f"no such app: {app_id}")
    return app


@router.patch("/{app_id}", response_model=App, response_model_exclude_unset=True)
async def patch_app(app_id: str, body: AppPatchIn) -> dict:
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.icon is not None:
        fields["icon"] = body.icon
    if body.slug is not None:
        fields["slug"] = body.slug
    if body.parent_id is not None:
        fields["parent_id"] = body.parent_id
    if body.archived is not None:
        from datetime import datetime, timezone
        fields["archived_at"] = datetime.now(timezone.utc) if body.archived else None
    try:
        app = apps_svc.update_app(app_id, **fields)
    except apps_svc.AppsUnavailable:
        raise _unavailable()
    except apps_svc.SlugTaken as e:
        raise HTTPException(409, str(e))
    if app is None:
        raise HTTPException(404, f"no such app: {app_id}")
    return app


@router.delete("/{app_id}", response_model=AppDeleted, response_model_exclude_unset=True)
async def delete_app(app_id: str) -> dict:
    try:
        ok = apps_svc.delete_app(app_id)
    except apps_svc.AppsUnavailable:
        raise _unavailable()
    if not ok:
        raise HTTPException(404, f"no such app: {app_id}")
    return {"deleted": True}


# --- nested collections ---------------------------------------------------

@router.get("/{app_id}/chats", response_model=AppChatList, response_model_exclude_unset=True)
async def app_chats(app_id: str, archived: bool = Query(False)) -> dict:
    try:
        chats = apps_svc.chats_for_app(app_id, archived=archived)
    except apps_svc.AppsUnavailable:
        raise _unavailable()
    if chats is None:
        raise HTTPException(404, f"no such app: {app_id}")
    return {"chats": chats}


@router.get("/{app_id}/tickets", response_model=AppTicketList, response_model_exclude_unset=True)
async def app_tickets(app_id: str) -> dict:
    try:
        return {"tickets": apps_svc.tickets_for_app(app_id)}
    except apps_svc.AppNotFound:
        raise HTTPException(404, f"no such app: {app_id}")
    except apps_svc.AppsUnavailable:
        raise _unavailable()


@router.get("/{app_id}/notes", response_model=AppNoteList, response_model_exclude_unset=True)
async def app_notes(app_id: str) -> dict:
    try:
        return {"notes": apps_svc.notes_for_app(app_id)}
    except apps_svc.AppNotFound:
        raise HTTPException(404, f"no such app: {app_id}")
    except apps_svc.AppsUnavailable:
        raise _unavailable()


@router.get("/{app_id}/runs", response_model=AppRunList, response_model_exclude_unset=True)
async def app_runs(app_id: str, limit: int = Query(50, ge=1, le=200)) -> dict:
    try:
        return {"runs": apps_svc.runs_for_app(app_id, limit=limit)}
    except apps_svc.AppNotFound:
        raise HTTPException(404, f"no such app: {app_id}")
    except apps_svc.AppsUnavailable:
        raise _unavailable()


@router.get("/{app_id}/agents", response_model=AppAgentList, response_model_exclude_unset=True)
async def app_agents(app_id: str) -> dict:
    try:
        return {"agents": apps_svc.agents_for_app(app_id)}
    except apps_svc.AppNotFound:
        raise HTTPException(404, f"no such app: {app_id}")
    except apps_svc.AppsUnavailable:
        raise _unavailable()
