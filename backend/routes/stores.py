"""Stores API — the catalog, and the one door onto a Store's contents.

  GET  /stores                 the declared Store catalog (+ its policy tier)
  GET  /stores/export?uri=     the portable dump of one Store
  GET  /stores/policies        the operator's policy records
  POST /stores/{id}/read       read | list | search — broker-mediated when the
                               policy says `mediated` and the caller holds no grant
  POST /stores/{id}/write      write | append — grant-checked, never adjudicated

`{id}` is `<owner_app>:<name>` ("notes:vault"), optionally with a path inside the
store in the body (`path: "journal/2026"`), which resolves to the URI suffix the
policy registry matches on.

WHO IS GATED HERE. The operator is not, ever: these routes sit behind the operator
dependency, and services/stores.read() serves an operator identity verbatim without
consulting a policy or a broker. The gate exists for a caller that SAYS it is an
agent (`identity: "agent:<slug>"`), which is what a dispatched sweep does and what
nothing else does accidentally — resolve_identity never infers an agent.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from services import policy, stores

router = APIRouter(tags=["stores"])


def _uri(store_id: str, path: str | None = None) -> str:
    """`notes:vault` (+ an optional inner path) -> a store URI. 400 on nonsense."""
    if ":" not in store_id:
        raise HTTPException(400, f"store id must be <app>:<name>, got {store_id!r}")
    app, name = store_id.split(":", 1)
    try:
        return stores.make_uri(app, name, path)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _tier(store_uri: str) -> str:
    try:
        return policy.for_uri(store_uri).tier
    except policy.PolicyError as e:
        # A policy record the operator wrote that we cannot honor. Loud, not
        # silently downgraded to `open`. (A corrupt FILE fails open instead — see
        # services/policy.py for why those two directions differ.)
        raise HTTPException(500, f"policy error: {e}")


@router.get("/stores")
async def list_stores(type: str | None = None, app: str | None = None, project: str | None = None):
    """The declared Store catalog with each Store's policy tier.

    `type` narrows by broker type (Note, Budget, Finance, ...).
    `app` and `project` narrow by host (entity-model 2026-07-18 §8.3);
    used by B11's "Storage and access" section inside App / Project detail.
    Registers from every source on each read so a newly installed app's
    stores show up. {"stores": [...], "types": [...]}."""
    stores.register_all()
    if app or project:
        matched = stores.by_host(app=app, project=project)
    else:
        matched = stores.list(type=type)
    out = []
    for s in matched:
        if type is not None and s.type != type:
            continue
        out.append({**s.to_dict(), "tier": _tier(s.uri)})
    return {"stores": out, "types": stores.types()}


@router.get("/stores/policies")
async def list_policies():
    """The operator's policy records. An empty list is the normal, correct state
    for an installation that gates nothing."""
    stores.register_all()
    try:
        return {"policies": [p.to_dict() for p in policy.load_policies()]}
    except policy.PolicyError as e:
        raise HTTPException(500, f"policy error: {e}")


@router.get("/stores/export")
async def export_store(uri: str):
    """Serialize a Store to a portable artifact directory and return its path.
    Read-only over the source (never moves/deletes/chmods). 404 if not declared."""
    stores.register_all()
    try:
        path = stores.export(uri)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"uri": uri, "path": path}


class ReadIn(BaseModel):
    verb: str = "read"                       # read | list | search
    selector: dict = {}                      # {query} | {name} | {folder} | {month}
    path: str | None = None                  # a folder/item inside the store
    identity: str | None = None              # "agent:<slug>"; absent = the operator
    purpose: str = ""                        # UNTRUSTED, only read by the broker
    token: str | None = None                 # an existing grant, if the caller holds one


@router.post("/stores/{store_id}/read")
async def read_store(store_id: str, body: ReadIn = Body(...)):
    """Read/list/search a Store.

    The operator is served directly. An agent identity is served from its grant if
    it holds one (mechanical, free), adjudicated once if the Store is `mediated`
    and it does not, and denied otherwise. 403 carries `reason_class` + `retryable`
    so an outage is never mistaken for a policy denial."""
    stores.register_all()
    uri = _uri(store_id, body.path)
    try:
        return stores.read(
            uri,
            identity=body.identity,
            verb=body.verb,
            selector=body.selector,
            purpose=body.purpose,
            token=body.token,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, f"not found: {e}")
    except stores.AccessDenied as e:
        raise HTTPException(403, {
            "reason": e.reason, "reason_class": e.reason_class, "retryable": e.retryable,
        })
    except policy.PolicyError as e:
        raise HTTPException(500, f"policy error: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


class WriteIn(BaseModel):
    content: str
    verb: str = "write"                      # write | append
    selector: dict = {}                      # {name}
    path: str | None = None
    identity: str | None = None
    token: str | None = None


@router.post("/stores/{store_id}/write")
async def write_store(store_id: str, body: WriteIn = Body(...)):
    """Write/append through a Store. Grant-checked; never adjudicated, because
    there is no filtered view of a write."""
    stores.register_all()
    uri = _uri(store_id, body.path)
    try:
        return stores.write(
            uri,
            content=body.content,
            identity=body.identity,
            verb=body.verb,
            token=body.token,
            selector=body.selector,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except stores.AccessDenied as e:
        raise HTTPException(403, {
            "reason": e.reason, "reason_class": e.reason_class, "retryable": e.retryable,
        })
    except policy.PolicyError as e:
        raise HTTPException(500, f"policy error: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
