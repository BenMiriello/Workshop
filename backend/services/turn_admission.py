"""Whether a turn the platform is about to start on its own should wait.

The drain (`chat_drain`) already refuses while the thread is busy. This is the
other half: the thread is free, but starting the turn now could only fail. The
answer is a WAIT with a reason the operator can read, never a refusal, and the
queue keeps its messages until the reason passes or the operator says Send now.

Today there is one such reason: the subscription's usage window is exhausted,
so any turn on it would be answered with the limit message and nothing else.
A turn on a model some other provider serves is not affected by that window.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from services import chat_queue, llm_providers, usage_limits
from services.llm_contract import TRANSPORT_CLI

log = logging.getLogger(__name__)

CARRY_ON_KEY = "usage-limit-reset"
CARRY_ON_REASON = "Carry on after the usage limit resets"
CARRY_ON_SEED = (
    "[USAGE LIMIT RESET - not a message from the operator]\n"
    "The last turn in this chat stopped because the subscription's usage limit "
    "was reached, and that limit has now reset. Continue the work it "
    "interrupted, from where it stopped. If nothing was in progress, answer "
    "the operator's last message.")


def model_for(row: dict, batch: list[dict] | None = None) -> str | None:
    """The model the next turn on this thread would run, as the drain sends it."""
    model = chat_queue.setting_of(batch or [], "model") or (row or {}).get("model")
    if model:
        return model
    try:
        return llm_providers.deployment_default().id
    except Exception:  # pragma: no cover - defensive
        return None


def on_subscription(model_id: str | None) -> bool:
    """Is this model reached through the CLI's own account? An id nothing in
    the catalog names is the CLI's to resolve, so it counts as the account's."""
    provider = llm_providers.provider_for_model(model_id)
    return provider is None or provider.transport == TRANSPORT_CLI


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def wait_reason(row: dict, batch: list[dict] | None = None) -> dict | None:
    """`{reason, until}` if a turn started now should wait, else None."""
    try:
        until = usage_limits.blocked_until()
        if until and on_subscription(model_for(row, batch)):
            return {"reason": "usage_limit", "until": _iso(until)}
    except Exception as e:  # pragma: no cover - a broken check must not wedge chat
        log.warning("turn admission check failed: %s", e)
    return None


def carry_on_after_limit(thread_id: str, resets_at: float) -> None:
    """Arm this thread to pick its work back up once the limit resets. One per
    thread: a turn that hits the limit twice moves the alarm, never doubles it."""
    from services import wakeups

    due = max(resets_at + usage_limits.RESET_MARGIN_S,
              datetime.now(timezone.utc).timestamp() + 5)
    try:
        wakeups.arm(thread_id, reason=CARRY_ON_REASON,
                    due_at=datetime.fromtimestamp(due, timezone.utc),
                    seed=CARRY_ON_SEED, key=CARRY_ON_KEY)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("could not arm the after-reset wake-up on %s: %s", thread_id, e)
