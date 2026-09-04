"""The event log, and the one way to publish to it.

WHY THIS MODULE EXISTS. The append-to-events.jsonl sequence was hand-copied in
three places (`routes/events.py` plus two app routers, one of which has
since left this repo)
and the three copies had already drifted: plaid's omitted the `signature` field
entirely, and birds' reached into `routes.events` for a private helper
(`_load_subscriptions`) to do it, making one app's route module depend on
another route module's internals.

The sequence is not incidental. Three parts of it are load-bearing and are
exactly the parts a fourth copy would get wrong:

  * the byte offset is captured BEFORE the write, because that offset is the
    `event_ref` a pending run later seeks to (`event_dispatch` docstring);
  * `authorization` and `cookie` are stripped from the recorded headers, and a
    source that carries its own credential-bearing header must strip that too
    (plaid-verification is a signed JWT over the body);
  * the record's key set is a wire format: `routes/events.py:EventRecord` and
    `scripts/chat_event_bridge.py` both read it back.

So publishing is a platform capability with one implementation, not a shape
every caller reproduces. An app that receives its own webhook on its own route
calls `publish()` and is done; it needs to know nothing about the log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from wsplatform import paths

log = logging.getLogger("workshop.event_log")

EVENTS_LOG = paths.env_path("WORKSHOP_EVENTS_LOG", "events.jsonl")
SUBSCRIPTIONS = paths.env_path("WORKSHOP_SUBSCRIPTIONS", "agent-subscriptions.json")

# Never recorded, for any source. A source with its own credential-bearing
# header passes it in `redact_headers`.
_ALWAYS_REDACT = {"authorization", "cookie"}


def _known(source: str) -> bool:
    # Imported here rather than at module load: routes.events imports THIS
    # module, so a load-time edge the other way would be a cycle.
    try:
        from routes.events import known_sources

        return source in known_sources()
    except Exception:
        return False


def publish(
    source: str,
    body: Any,
    *,
    headers: Mapping[str, str] | None = None,
    signature: dict[str, Any] | None = None,
    redact_headers: Iterable[str] = (),
    known: bool | None = None,
) -> int:
    """Append one event to the log and return the byte offset it was written at.

    The offset is the event's address: `event_dispatch.enqueue_event` records it
    as the pending run's `event_ref`, so a consumer can re-read the exact line
    without the payload being copied into the queue. It is read BEFORE the
    append, so it points at the start of this record and not past it.

    `known` defaults to asking the source registry (platform sources plus the
    event sources every installed app declares). Pass it explicitly only for a
    source whose own route already established it, which saves the lookup.
    """
    drop = _ALWAYS_REDACT | {h.lower() for h in redact_headers}
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "known_source": _known(source) if known is None else known,
        "signature": signature if signature is not None else {"ok": True, "reason": "no_verifier_registered"},
        "headers": {k: v for k, v in (headers or {}).items() if k.lower() not in drop},
        "body": body,
    }
    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    offset = EVENTS_LOG.stat().st_size if EVENTS_LOG.exists() else 0
    with EVENTS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return offset


def publish_and_dispatch(
    source: str,
    body: Any,
    *,
    headers: Mapping[str, str] | None = None,
    signature: dict[str, Any] | None = None,
    redact_headers: Iterable[str] = (),
    known: bool | None = None,
) -> dict[str, Any]:
    """Publish, then enqueue a pending run for every subscriber to `source`.

    This is what all three receivers actually want; publishing without
    dispatching would log an event nothing acts on. Returns the dispatch result
    (`enqueued`, `agent_ids`, and on a duplicate `deduped`/`dedup_key`) with the
    offset added, so a caller that wants to log or return the address has it.
    """
    offset = publish(
        source,
        body,
        headers=headers,
        signature=signature,
        redact_headers=redact_headers,
        known=known,
    )
    from services.event_dispatch import enqueue_event

    summary = body if isinstance(body, dict) else {"_raw": str(body)[:512]}
    dispatch = enqueue_event(source, event_offset=offset, event_summary=summary)
    return {**dispatch, "offset": offset}


def load_subscriptions() -> dict[str, list[str]]:
    """source -> agent ids listening to it. One implementation: this file had
    three, one of them private to a route module and imported across routes."""
    try:
        return json.loads(SUBSCRIPTIONS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def subscribers(source: str) -> list[str]:
    got = load_subscriptions().get(source, [])
    return list(got) if isinstance(got, list) else []
