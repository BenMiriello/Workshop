"""Where the workshop tool server reads WHO is calling: scope, owner, chat, turn.

The server has two transports and one body of tool code. Over stdio it is a
child of the turn's `claude` process and its context is that child's own
environment, set at spawn time. Over HTTP (`routes/mcp_workshop.py`) it runs
inside the backend, which serves every turn at once, so the context of one
call is the signed claims that call carried (`services/turn_claims.py`), bound
here for the duration of the call.

Every read of the caller's context goes through `get`, so the two transports
cannot disagree about who is asking.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_CLAIMS: ContextVar[Optional[dict[str, str]]] = ContextVar("mcp_turn_claims", default=None)


def get(key: str, default: str = "") -> str:
    claims = _CLAIMS.get()
    if claims is not None:
        return claims.get(key, default)
    return os.environ.get(key, default)


def over_http() -> bool:
    return _CLAIMS.get() is not None


@contextmanager
def bound(claims: dict[str, str]) -> Iterator[None]:
    token = _CLAIMS.set(dict(claims))
    try:
        yield
    finally:
        _CLAIMS.reset(token)
