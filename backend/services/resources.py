"""Shared machine resources a chat turn can need, and who is using them.

A model reached through the CLI's own account runs somewhere else and needs
nothing here. A model served on this machine shares its hardware with
everything else that runs on it, so its provider DECLARES what it runs on
(`resources`, e.g. ("gpu",)); the platform never guesses from a model's name
or reads the hardware itself.

Two things follow from a declaration:

  * a turn on that model takes a lease on each resource while it runs, so any
    other chat, and any process on the machine, can see it is busy;
  * a message sent with the "when it is free" rule (`send_rules`) waits until
    nobody else holds them, so it neither interrupts nor is interrupted.

A plain send never waits on this: it starts at once, as it always has.

WHICH COORDINATOR answers is the installation's choice,
`WORKSHOP_RESOURCE_COORDINATOR`: unset is the built-in lease file
(`resource_leases`), which needs nothing installed; `module:<dotted.path>`
names a module with the same three functions (`holders`, `take`, `drop`) for
an installation that already runs its own broker.
"""
from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger(__name__)

ENV = "WORKSHOP_RESOURCE_COORDINATOR"
_backend = None
_backend_spec = None


def backend():
    """The coordinator module this installation declared."""
    global _backend, _backend_spec
    spec = (os.environ.get(ENV) or "").strip()
    if _backend is not None and spec == _backend_spec:
        return _backend
    mod = None
    if spec.startswith("module:"):
        try:
            mod = importlib.import_module(spec.split(":", 1)[1].strip())
        except Exception as e:
            log.warning("resource coordinator %r could not be loaded (%s); "
                        "using the built-in one", spec, e)
    if mod is None:
        from services import resource_leases as mod
    _backend, _backend_spec = mod, spec
    return mod


def coordinator_name() -> str:
    return getattr(backend(), "NAME", None) or backend().__name__.rsplit(".", 1)[-1]


def needs_for_model(model_id: str | None) -> tuple[str, ...]:
    from services import llm_providers
    provider = llm_providers.provider_for_model(model_id)
    try:
        return tuple(getattr(provider, "resources", ()) or ())
    except Exception:  # pragma: no cover - a provider must not break chat
        return ()


def needs_for_thread(thread_id: str) -> tuple[str, ...]:
    from services import chat_queue, chat_store, turn_admission
    row = chat_store.get_thread(thread_id) or {}
    return needs_for_model(turn_admission.model_for(row, chat_queue.read(thread_id)))


def _turn_lease(turn_id: str, resource: str) -> str:
    return f"turn:{turn_id}:{resource}"


def busy_with(resources, *, except_thread: str | None = None) -> list[dict]:
    """Who holds any of `resources`, leaving out this thread's own turns."""
    out: list[dict] = []
    for r in resources:
        try:
            for h in backend().holders(r):
                if except_thread and h.get("holder") == f"thread:{except_thread}":
                    continue
                out.append(h)
        except Exception as e:  # pragma: no cover - an unreachable broker is "free"
            log.warning("resource coordinator could not answer for %s: %s", r, e)
    return out


def free_for_thread(thread_id: str) -> bool:
    return not busy_with(needs_for_thread(thread_id), except_thread=thread_id)


def take_for_turn(thread_id: str, turn_id: str, pid: int | None,
                  model_id: str | None, label: str = "",
                  scope: str | None = None) -> None:
    for r in needs_for_model(model_id):
        try:
            backend().take(_turn_lease(turn_id, r), r, holder=f"thread:{thread_id}",
                           label=label or "Chat", pid=pid, scope=scope)
        except Exception as e:  # pragma: no cover - never fail a turn over this
            log.warning("could not record turn %s on %s: %s", turn_id, r, e)


def release_for_turn(turn_id: str, model_id: str | None) -> None:
    for r in needs_for_model(model_id):
        try:
            backend().drop(_turn_lease(turn_id, r))
        except Exception as e:  # pragma: no cover - the lease expires on its own
            log.warning("could not release turn %s on %s: %s", turn_id, r, e)
