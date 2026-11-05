"""Raise a Workshop event for the apps subscribed to it.

Mounts at /app-events. The web UI reports what happened; `services/app_events`
holds the vocabulary and tells every running app that subscribed.

    POST /app-events/{event}   body: the event's fields (app_events.EVENTS)

Answers as soon as the event is accepted. Delivery happens afterwards and its
outcome is not reported back: a slow app never holds the page that raised it.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ValidationError

from services import app_events

router = APIRouter(prefix="/app-events", tags=["app-events"])


class AppEventAccepted(BaseModel):
    event: str


@router.post("/{event}", response_model=AppEventAccepted, status_code=202)
def raise_event(event: str, body: dict = Body(...)) -> AppEventAccepted:
    model = app_events.EVENTS.get(event)
    if model is None:
        raise HTTPException(404, f"no such event: {event}")
    try:
        payload = model.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    app_events.publish(event, payload)
    return AppEventAccepted(event=event)
