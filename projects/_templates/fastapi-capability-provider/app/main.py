"""ASGI entrypoint for the {{title}} service.

The Workshop app runner starts this via workshop.app.json:
    uvicorn app.main:app --host 127.0.0.1 --port $PORT
`/health` is the runner's liveness probe; the capability routes live in
router.py.
"""
from __future__ import annotations

from fastapi import FastAPI

from .router import PROVIDES, router

app = FastAPI(title="{{title}}")
app.include_router(router)


@app.get("/health")
def health() -> dict:
    """Liveness probe for the app runner."""
    return {"status": "ok", "provides": PROVIDES}
