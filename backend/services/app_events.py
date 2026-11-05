"""Things that happen in the Workshop, told to the apps that asked to hear them.

An app subscribes in its own `workshop.app.json`:

    "events": {"copied": {"service": "<service name>", "path": "/hooks/copied"}}

and while that service is running, each such event is POSTed to it as JSON,

    {"event": "copied", "at": "<UTC ISO time>", ...the event's own fields,
     "owner": <id of the user who raised it, or null>}

at `http://127.0.0.1:<the service's port><path>`. `owner` is the data owner
pinned for the request that raised the event (services/ownership.py), never a
value from the request's body, so an app can keep each user's data apart. Delivery is best-effort and
off the caller's thread, with a short timeout: an app that is down, slow or
answering an error costs a log line, never the request that raised the event.
An app that is not running is not called, and nothing is queued for it.

`EVENTS` is the vocabulary, and the only place an event is named: each entry is
the event's name and the model its fields must satisfy. A subscription to a
name not in it is ignored, and so is a request to publish one.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field, model_serializer

from services import app_runner, ownership

log = logging.getLogger("workshop.app_events")

__all__ = ["EVENTS", "POST_TIMEOUT_S", "Subscription", "deliver", "publish",
           "subscribers", "subscriptions"]

POST_TIMEOUT_S = 2.0
MAX_TEXT = 100_000


class Source(BaseModel):
    """Where in the Workshop the event happened: the page's path. `app` names
    the app on the computer it happened in, when that was not Workshop (a copy
    the Mac app saw in another app), and left out for one made in Workshop."""
    path: str = Field(default="", max_length=2000)
    app: str | None = Field(default=None, max_length=200)

    @model_serializer(mode="wrap")
    def _without_empty_app(self, handler):
        data = handler(self)
        if data.get("app") is None:
            data.pop("app", None)
        return data


class Copied(BaseModel):
    """Text was copied, by a keyboard shortcut, a menu or a copy button."""
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: Source = Field(default_factory=Source)


class ChatView(BaseModel):
    """How the operator sees a chat's work in the apps that show it changed:
    `view` is follow, show or hide (services/page_link.VIEWS)."""
    thread: str = Field(min_length=1, max_length=64)
    view: Literal["follow", "show", "hide"]


class Place(BaseModel):
    """Where dictated words went: a field (`App › page › field`), a chat (its
    title) or the clipboard, and the page it is on."""
    kind: Literal["field", "chat", "clipboard"]
    name: str = Field(default="", max_length=500)
    path: str = Field(default="", max_length=2000)


class DictatedAudio(BaseModel):
    """The dictation's recording, on a loopback-only route, kept for an hour."""
    url: str
    mime: str
    ms: int


class Dictated(BaseModel):
    """A dictation ended (or, `imported`, one from a device's past history)."""
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=MAX_TEXT)
    started_at: str = Field(max_length=64)
    ended_at: str = Field(max_length=64)
    status: Literal["done", "cancelled"]
    places: list[Place] = Field(default_factory=list, max_length=100)
    audio: DictatedAudio | None = None
    imported: bool = False


EVENTS: dict[str, type[BaseModel]] = {
    "copied": Copied,
    "chat_view": ChatView,
    "dictated": Dictated,
}


@dataclass(frozen=True)
class Subscription:
    project: str
    service: str
    path: str


def subscriptions(raw: object) -> dict[str, tuple[str, str]]:
    """A manifest's `events` block as {event: (service, path)}.

    Tolerant: an unknown event, an entry without a service or a path, or a
    block that is not an object is skipped rather than failing the manifest."""
    block = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for event, entry in block.items():
        if event not in EVENTS or not isinstance(entry, dict):
            continue
        service = str(entry.get("service") or "").strip()
        path = str(entry.get("path") or "").strip()
        if not service or not path:
            continue
        out[event] = (service, path if path.startswith("/") else f"/{path}")
    return out


def subscribers(event: str) -> list[tuple[Subscription, int]]:
    """Every running service subscribed to `event`, with the port it is on."""
    found: list[tuple[Subscription, int]] = []
    for project in list(app_runner._STATE.keys()):
        try:
            if app_runner.is_self_project(project):
                continue
            raw = app_runner._read_manifest_file(app_runner._project_dir(project))
            sub = subscriptions(raw).get(event)
            if sub is None:
                continue
            status, port = app_runner.service_target(project, sub[0])
        except Exception as e:  # noqa: BLE001 - one unreadable app skips only itself
            log.debug("app events: %s skipped for %r: %s", event, project, e)
            continue
        if status == "running" and port:
            found.append((Subscription(project, sub[0], sub[1]), int(port)))
    return found


def deliver(event: str, fields: dict) -> list[tuple[str, int | str]]:
    """POST `event` to each subscriber, in turn, on this thread, as raised by
    the owner pinned in this context (`publish` and `asyncio.to_thread` both
    carry the caller's).

    Returns (project, status code or error name) per subscriber."""
    body = {"event": event,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            **fields,
            "owner": ownership.owner_or_none()}
    results: list[tuple[str, int | str]] = []
    for sub, port in subscribers(event):
        url = f"http://127.0.0.1:{port}{sub.path}"
        try:
            r = httpx.post(url, json=body, headers={"X-Workshop-Event": event},
                           timeout=POST_TIMEOUT_S)
            results.append((sub.project, r.status_code))
            if r.status_code >= 400:
                log.info("app events: %s's %s answered %s to %s",
                         sub.project, sub.service, r.status_code, event)
        except Exception as e:  # noqa: BLE001 - best-effort by contract
            results.append((sub.project, type(e).__name__))
            log.info("app events: %s not delivered to %s: %s", event, sub.project, e)
    return results


def publish(event: str, payload: BaseModel) -> threading.Thread:
    """Deliver `event` off the caller's thread. Returns the thread doing it."""
    ctx = contextvars.copy_context()
    worker = threading.Thread(
        target=ctx.run, args=(deliver, event, payload.model_dump()),
        name=f"app-event-{event}", daemon=True)
    worker.start()
    return worker
