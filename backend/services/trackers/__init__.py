"""The tracker adapter surface: eight methods, one field table, one status map.

Design: `docs/platform-redesign/ticket-model-and-external-trackers-2026-08-07.md`
and `ticket-lifecycle-and-integration-2026-08-10.md`.

WHY EIGHT METHODS. A provider is supportable exactly to the extent it can answer
these; anything it cannot is out of scope rather than a special case threaded
through the sync engine. GitHub, Linear, Jira, Asana, Shortcut and Azure DevOps
all answer all eight.

WHY THE FIELD TABLE IS CODE. Field ownership is not a per-user setting. A sync
product with a per-field direction matrix is a product whose users cannot predict
what it will do, and this is a one-operator system. So: the tracker owns what
humans edit where they read it (title, body, labels, assignee); workshop owns
status, because workshop runs the state machine and the tracker only learns the
result; and everything under `exec` is private and never leaves the box.
"""
from __future__ import annotations

# The vocabulary lives in `contract`, which imports nothing from `services`.
# Re-exported here so every existing `from services.trackers import Filter`
# keeps working: this package is still the front door, it just no longer both
# declares the protocol and constructs the things that implement it.
from services.trackers.contract import (  # noqa: F401  (re-export)
    Account,
    Filter,
    IDEM_PREFIX,
    MODES,
    OWNERSHIP,
    REMOTE_TO_STATUS,
    RemoteIssue,
    STATUS_TO_REMOTE,
    SYNCABLE,
    Tracker,
    find_idem,
    idem_marker,
    local_status,
    remote_state,
    strip_idem,
)
from services.trackers import github as _github


def for_binding(binding: dict) -> Tracker:
    """The adapter for a binding. One place that knows the provider names, so
    adding a provider is a new module plus a row here."""
    provider = str(binding.get("provider") or "").strip()
    if provider == "github":
        return _github.GitHubTracker(binding)
    raise ValueError(f"no tracker adapter for provider: {provider!r}")


PROVIDERS = ("github",)  # linear and jira are designed; neither is built
