"""Shared machine resources over HTTP (services/resources).

Two routers. `router` is the operator's: what a chat's model runs on and who
else is using it, which is what the composer reads to offer "when it is free".
`machine_router` is loopback-only: any process on this machine takes and drops
leases there, so its work and Workshop's chats see each other.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import chat_store, resources

router = APIRouter(prefix="/resources", tags=["resources"])
machine_router = APIRouter(prefix="/resources/leases", tags=["resources"])


class Holder(BaseModel):
    id: str
    resource: str
    holder: str
    label: str = ""
    scope: str | None = None
    since: float | None = None


class ThreadResources(BaseModel):
    """`needs` empty means the chat's model runs elsewhere and never waits."""

    needs: list[str]
    busy: list[Holder]
    coordinator: str


class LeaseIn(BaseModel):
    resource: str
    holder: str
    label: str = ""
    scope: str | None = None
    pid: int | None = None
    ttl_s: float | None = Field(default=None, gt=0)


def _holder(h: dict) -> Holder:
    return Holder(id=str(h.get("id")), resource=str(h.get("resource") or ""),
                  holder=str(h.get("holder") or ""), label=str(h.get("label") or ""),
                  scope=h.get("scope"), since=h.get("since"))


@router.get("/threads/{thread_id}", response_model=ThreadResources)
async def thread_resources(thread_id: str):
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    needs = resources.needs_for_thread(thread_id)
    busy = resources.busy_with(needs, except_thread=thread_id)
    return ThreadResources(needs=list(needs), busy=[_holder(h) for h in busy],
                           coordinator=resources.coordinator_name())


@machine_router.get("/{resource}", response_model=list[Holder])
async def holders(resource: str):
    return [_holder(h) for h in resources.backend().holders(resource)]


@machine_router.put("/{lease_id}", response_model=Holder)
async def take(lease_id: str, body: LeaseIn):
    lease = resources.backend().take(lease_id, body.resource, holder=body.holder,
                                     label=body.label, pid=body.pid, ttl_s=body.ttl_s,
                                     scope=body.scope)
    return _holder(lease)


@machine_router.delete("/{lease_id}")
async def drop(lease_id: str) -> dict:
    return {"dropped": bool(resources.backend().drop(lease_id))}
