"""A chat's schedules over HTTP (`services/chat_schedules`), and the rest of
what is set to send into it: queued messages waiting on a rule and the chat's
own wake-ups, so the chat can show all of it in one place.

The operator manages every schedule on his chats. An app (a service token)
makes its own, sends through them, and changes only the ones it set; what it
set says so, with its token's name, its label and a link.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from deps_user import request_auth
from services import chat_drain, chat_queue, chat_schedule_sends, chat_schedules, chat_store, wakeups

router = APIRouter(tags=["chat"])


class SetBy(BaseModel):
    """`you` is the operator; `app` names the service token's holder; `chat`
    is the chat the schedule sends into, which set it for itself."""

    kind: Literal["you", "app", "chat"]
    name: str | None = None
    href: str | None = None


class Schedule(BaseModel):
    id: str
    text: str
    label: str | None = None
    repeat: Literal["after_reply", "every", "once"]
    every_min: int | None = None
    start_at: str | None = None
    until_at: str | None = None
    max_sends: int | None = None
    status: Literal["active", "paused", "done", "deleted"]
    why: str | None = None
    sent: int = 0
    last_sent_at: str | None = None
    next_at: str | None = None
    driver: Literal["workshop", "app"]
    set_by: SetBy
    created_at: str
    updated_at: str | None = None


class WakeUp(BaseModel):
    id: str
    due_at: str
    reason: str = ""
    armed_at: str | None = None


class Scheduled(BaseModel):
    """Everything set to send into this chat. `count` is what the composer's
    dot says: active schedules, messages waiting on a rule, wake-ups."""

    schedules: list[Schedule]
    wakeups: list[WakeUp]
    waiting: int
    count: int


class ScheduleIn(BaseModel):
    text: str
    label: str | None = None
    repeat: Literal["after_reply", "every", "once"] = "after_reply"
    every_min: int | None = None
    start_at: str | None = None
    until_at: str | None = None
    max_sends: int | None = None
    #: An app's link to what set it (a Workshop path).
    href: str | None = None


class SchedulePatch(BaseModel):
    text: str | None = None
    label: str | None = None
    repeat: Literal["after_reply", "every", "once"] | None = None
    every_min: int | None = None
    start_at: str | None = None
    until_at: str | None = None
    max_sends: int | None = None
    status: Literal["active", "paused", "done"] | None = None
    why: str | None = None


class SendIn(BaseModel):
    text: str | None = None


class RearmIn(BaseModel):
    """The rule a schedule starts over with."""

    text: str | None = None
    label: str | None = None
    repeat: Literal["after_reply", "every", "once"] | None = None
    every_min: int | None = None
    start_at: str | None = None
    until_at: str | None = None
    max_sends: int | None = None


class Send(BaseModel):
    at: str
    state: Literal["sent", "waiting"]


class Sends(BaseModel):
    sends: list[Send]


def _thread(thread_id: str) -> None:
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")


def _app(request: Request) -> str | None:
    auth = request_auth(request)
    return (auth.get("name") or auth.get("scope") or "an app") if auth.get("method") == "service" else None


def _mine(request: Request, thread_id: str, sid: str) -> dict:
    sch = chat_schedules.get(thread_id, sid)
    if sch is None or sch.get("status") == "deleted":
        raise HTTPException(404, "no such schedule")
    app = _app(request)
    by = sch.get("set_by") or {}
    if app is not None and (by.get("kind") != "app" or by.get("name") != app):
        raise HTTPException(403, "an app changes only the schedules it set")
    return sch


@router.get("/threads/{thread_id}/schedules", response_model=Scheduled)
def list_scheduled(thread_id: str):
    _thread(thread_id)
    items = chat_schedules.read(thread_id)
    waiting = sum(1 for e in chat_queue.read(thread_id)
                  if e.get("when") and not str(e.get("sender") or "").startswith(
                      chat_schedules.SENDER_PREFIX))
    wakes = wakeups.read(thread_id)
    active = sum(1 for s in items if s["status"] == "active")
    return {"schedules": items, "wakeups": wakes, "waiting": waiting,
            "count": active + waiting + len(wakes)}


@router.post("/threads/{thread_id}/schedules", response_model=Schedule)
def create_schedule(thread_id: str, body: ScheduleIn, request: Request):
    _thread(thread_id)
    app = _app(request)
    href = body.href if body.href and body.href.startswith("/") else None
    set_by = {"kind": "app", "name": app, "href": href} if app else {"kind": "you"}
    try:
        return chat_schedules.create(thread_id, body.model_dump(exclude={"href"}),
                                     set_by=set_by, driver="app" if app else "workshop")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.patch("/threads/{thread_id}/schedules/{sid}", response_model=Schedule)
async def change_schedule(thread_id: str, sid: str, body: SchedulePatch, request: Request):
    _thread(thread_id)
    _mine(request, thread_id, sid)
    try:
        got = chat_schedules.change(thread_id, sid, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    chat_drain.drain_soon(thread_id, reason="schedule changed")
    return got


@router.delete("/threads/{thread_id}/schedules/{sid}", response_model=Schedule)
def delete_schedule(thread_id: str, sid: str, request: Request):
    _thread(thread_id)
    _mine(request, thread_id, sid)
    return chat_schedules.change(thread_id, sid, {"status": "deleted"})


@router.post("/threads/{thread_id}/schedules/{sid}/send", response_model=Schedule)
async def send_schedule(thread_id: str, sid: str, body: SendIn, request: Request):
    """One send now: an app's at its turn end, or the operator's Send now.
    409 with the reason when the schedule is paused, done or not started."""
    _thread(thread_id)
    _mine(request, thread_id, sid)
    try:
        got = chat_schedule_sends.send(thread_id, sid, body.text)
    except chat_schedule_sends.Refused as e:
        raise HTTPException(409, str(e)) from e
    chat_drain.drain_soon(thread_id, reason="schedule sent")
    return got


@router.post("/threads/{thread_id}/schedules/{sid}/again", response_model=Schedule)
async def send_schedule_again(thread_id: str, sid: str, request: Request):
    """An ended schedule's message once more, now; it stays ended."""
    _thread(thread_id)
    _mine(request, thread_id, sid)
    try:
        got = chat_schedule_sends.send_again(thread_id, sid)
    except chat_schedule_sends.Refused as e:
        raise HTTPException(409, str(e)) from e
    chat_drain.drain_soon(thread_id, reason="schedule sent again")
    return got


@router.post("/threads/{thread_id}/schedules/{sid}/rearm", response_model=Schedule)
async def rearm_schedule(thread_id: str, sid: str, body: RearmIn, request: Request):
    """Start it over: active, with this rule, nothing sent in the new run."""
    _thread(thread_id)
    _mine(request, thread_id, sid)
    try:
        got = chat_schedules.rearm(thread_id, sid, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    chat_drain.drain_soon(thread_id, reason="schedule started over")
    return got


@router.get("/threads/{thread_id}/schedules/{sid}/sends", response_model=Sends)
def schedule_sends(thread_id: str, sid: str):
    _thread(thread_id)
    if chat_schedules.get(thread_id, sid) is None:
        raise HTTPException(404, "no such schedule")
    return {"sends": chat_schedule_sends.history(thread_id, sid)}


@router.delete("/threads/{thread_id}/wakeups/{wake_id}", response_model=WakeUp)
def cancel_wakeup(thread_id: str, wake_id: str):
    _thread(thread_id)
    got = wakeups.cancel(thread_id, wake_id)
    if got is None:
        raise HTTPException(404, "no such wake-up")
    return got
