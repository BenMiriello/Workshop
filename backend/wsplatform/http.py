"""Shared HTTP-layer vocabulary: the collection envelope and the error shape.

`docs/decisions/0023-the-http-contract.md` is the record; this is the
mechanical half of its clauses 1-3. Nothing here does I/O, touches a request
or a response object, or knows about routes -- it is imported downward by
`main.py` (which registers the exception handlers) and by any router that
returns a paginated collection.

WHY THIS IS `wsplatform/`, NOT `platform/`. ADR 0024 names `platform/` as the
target directory for shared, behaviour-free vocabulary a route layer and a
domain layer both need, and that is exactly what this module is. It is not
placed there because `backend/` is on `sys.path` for every way this app
actually runs -- uvicorn loads `main:app` with the backend directory on the
path (that is how `from routes import agents` and `from deps_auth import
require_loopback` resolve at all), and `python -m pytest` from `backend/`
inserts the same directory as pytest's rootdir. A top-level package literally
named `platform` then shadows the STDLIB `platform` module for the entire
process, not just for our own imports of it. Verified: `mkdir backend/platform
&& touch backend/platform/__init__.py` and running `.venv/bin/python -m
pytest -q` from `backend/` fails before a single test collects, because
`_pytest`'s own import chain pulls in the stdlib `uuid` module, which calls
`platform.system()` at import time and gets our empty package instead. This
is not a pytest-only hazard: uvicorn's real startup path resolves `platform`
the same way, so a live app would carry the same landmine for any dependency
that imports it lazily (several do: `watchfiles`, used by `--reload`;
`huggingface_hub`; `kokoro_onnx`). `wsplatform` fills ADR 0024's role under a
name that does not collide. If the eventual `backend/domains/` + `backend/
api/` + `backend/db/` migration changes how the app is invoked (e.g. `python
-m backend.main`, which would put the REPO root rather than `backend/` on
`sys.path` and remove the collision), renaming this back to `platform/` is a
one-line `mv` at that point -- but doing it now breaks the suite this
same commit is required to keep green.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

# ADR 0023 clause 2: "Both constants live in one module ... so a router
# cannot invent the ninth default." Values exactly as the ADR states.
DEFAULT_PAGE = 50
MAX_PAGE = 200

T = TypeVar("T")


class PageMeta(BaseModel):
    """ADR 0023 clause 1's `page` object: `limit`, `next_cursor` (None means
    this is the last page, or the route has not adopted cursor pagination),
    `total` (None means the route declined to count -- legal, and never to
    be confused with zero).

    `offset` is NOT part of the ADR's envelope text. It exists only for the
    routes clause 2 grandfathers: "Offset survives only where it already is
    ... Existing offset routes keep `offset` and must additionally return
    `page.total`." The envelope is exactly `items` + `page` and nothing else
    at the top level, so a route that must keep `offset` for its existing
    callers carries it here rather than as a third top-level key. A route
    with no offset semantics leaves this None.
    """

    limit: int
    next_cursor: str | None = None
    total: int | None = None
    offset: int | None = None


class Page(BaseModel, Generic[T]):
    """ADR 0023 clause 1: the one collection envelope, generic so the
    generated TypeScript client gets a single `Page<T>` instead of 52
    entity-named shapes. `items` is always a list, always present, empty for
    no results, never null. `page` is always present."""

    items: list[T]
    page: PageMeta


# ---------------------------------------------------------------- errors
#
# ADR 0023 clause 3: the wire shape stays `{"detail": ...}` (what FastAPI
# already produces), and a registered handler upgrades a plain-string
# `detail` into `{"code", "message", "hint"}`. These two functions are that
# upgrade; `main.py` wires them into `HTTPException` and
# `RequestValidationError` handlers.

# Small and intentionally incomplete: an unnamed status falls back to
# `http_<status>`, which is still a stable, machine-readable code -- just not
# a hand-picked word. A raise site that wants a specific code passes a dict
# `detail={"code": "...", "message": "..."}` instead of a bare string.
_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    410: "gone",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"http_{status_code}")


def error_body(status_code: int, detail: object) -> dict:
    """Build the ADR 0023 clause 3 `detail` object from whatever a raise site
    handed `HTTPException`.

    A plain string (all 348 existing raise sites) becomes `message` with a
    `code` derived from the status -- compliant with zero edits at the raise
    site. A dict that already carries `code` and/or `message` -- a raise site
    that opted in to a stable code one at a time, exactly as the ADR invites
    -- passes through with any missing piece filled in. Anything else
    (an int, a list, `None`) is stringified into `message` rather than
    dropped, so no raise site's detail silently disappears.
    """
    if isinstance(detail, dict) and ("code" in detail or "message" in detail):
        return {
            "code": detail.get("code") or code_for_status(status_code),
            "message": detail.get("message", ""),
            "hint": detail.get("hint"),
        }
    if isinstance(detail, str):
        return {"code": code_for_status(status_code), "message": detail, "hint": None}
    return {
        "code": code_for_status(status_code),
        "message": "" if detail is None else str(detail),
        "hint": None,
    }


def validation_error_body(errors: list[dict]) -> dict:
    """`RequestValidationError.errors()` -> the same `{code, message, hint}`
    shape, so a 422 carries the same envelope as every other failure instead
    of FastAPI's own bare list-of-objects. `message` is the first field's
    problem, human-readable; `hint` names how many more there were rather
    than truncating a multi-field failure to one with no trace of the rest.
    """
    if not errors:
        return {"code": "validation_error", "message": "invalid request", "hint": None}
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    msg = first.get("msg", "invalid request")
    message = f"{loc}: {msg}" if loc else msg
    hint = f"{len(errors) - 1} more validation error(s)" if len(errors) > 1 else None
    return {"code": "validation_error", "message": message, "hint": hint}
