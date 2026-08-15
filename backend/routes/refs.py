"""The ref surface (memory-and-documentation-model-2026-08-14.md sec 10, sec
12 Phase 3): resolve a batch of `workshop:...` refs in one call (never N calls
-- the frontend `<Ref>` chip renders many of these on one page), and read who
references a given entity.

Operator-only, same as the other cross-cutting knowledge surfaces
(`docs_index`, `notes_all`): nothing here is written by an end user directly,
only read back after an agent or the operator wrote a ref into a note, ticket
or receipt body."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from services import backlinks, refs

router = APIRouter(prefix="/refs", tags=["refs"])


class ResolveIn(BaseModel):
    refs: list[str]


@router.post("/resolve")
async def resolve(body: ResolveIn) -> dict:
    """`{resolved: {ref: {kind, title, href, status} | null}}` for every ref in
    the batch, de-duplicated. A ref that cannot be positively confirmed to
    still resolve comes back null, never a chip that navigates nowhere."""
    out: dict[str, dict | None] = {}
    for r in body.refs:
        if r in out:
            continue
        out[r] = refs.resolve_ref(r)
    return {"resolved": out}


@router.get("/backlinks")
async def get_backlinks(ref: str) -> dict:
    """Every ref whose current body names `ref` -- the "referenced by" read.
    An index lookup, not a scan (see services/backlinks)."""
    return {"ref": ref, "refs": backlinks.referenced_by(ref)}
