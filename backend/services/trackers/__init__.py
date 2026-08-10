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

from dataclasses import dataclass, field
from typing import Protocol

# The only fields that may ever cross to an external tracker. Mirrors
# `tasks.SYNCABLE_FIELDS`; a field not named here is workshop-private by default.
SYNCABLE = ("title", "body", "status", "labels", "assignee", "priority")

# Who wins for each syncable field in `sync` mode. Not configurable.
#   "remote"    humans edit it where they read it
#   "workshop"  workshop runs the state machine
OWNERSHIP = {
    "title": "remote",
    "body": "remote",
    "labels": "remote",
    "assignee": "remote",
    "status": "workshop",
    # Only where the provider has a native priority; the adapter says so.
    "priority": "remote",
}

MODES = ("off", "import", "sync")

# Workshop's eight statuses collapse to the two states every tracker really has,
# plus a reason. The fine-grained status stays local: mirroring `doing` or
# `parked` as labels would litter a tracker other people read, for no gain.
#
#   draft never reaches a tracker at all (a draft is not work yet), so it has no
#   mapping here on purpose.
STATUS_TO_REMOTE = {
    "todo": ("open", None),
    "doing": ("open", None),
    "in_review": ("open", None),
    "blocked": ("open", None),
    "parked": ("open", None),
    "done": ("closed", "completed"),
    "cancelled": ("closed", "not_planned"),
}

# Coming the other way, a remote issue is only ever open or closed, so it maps to
# the two statuses that mean "somebody should look at this" and "finished".
# Anything more specific would be inventing local state from nothing.
REMOTE_TO_STATUS = {"open": "todo", "closed": "done"}


def remote_state(status: str) -> tuple[str, str | None]:
    """(state, state_reason) for a workshop status, or open with no reason."""
    return STATUS_TO_REMOTE.get(status, ("open", None))


def local_status(state: str, *, closed_reason: str | None = None) -> str:
    """The local status for a remote state. A `not_planned` close is a
    cancellation, not a completion — the one nuance worth carrying across."""
    if state == "closed" and (closed_reason or "").lower() in ("not_planned", "notplanned"):
        return "cancelled"
    return REMOTE_TO_STATUS.get(state, "todo")


# The marker that makes a create idempotent and a re-link deterministic. Written
# into the remote body, not a label: labels are user-visible and people delete
# them, and this is the one thing a reconnect after divergence relies on.
IDEM_PREFIX = "workshop-ticket"


def idem_marker(task_id: str) -> str:
    return f"<!-- {IDEM_PREFIX}: {task_id} -->"


def find_idem(body: str) -> str | None:
    """The workshop task id a remote body claims, if any."""
    import re
    m = re.search(rf"<!--\s*{IDEM_PREFIX}:\s*([0-9a-f]{{6,64}})\s*-->", body or "")
    return m.group(1) if m else None


def strip_idem(body: str) -> str:
    """The body without our marker, for display and for the shadow compare — the
    marker is ours and must never read as a change the operator made."""
    import re
    return re.sub(rf"\n*<!--\s*{IDEM_PREFIX}:.*?-->\n*", "", body or "").strip()


@dataclass
class Account:
    login: str
    name: str = ""
    url: str = ""


@dataclass
class RemoteIssue:
    """One issue as the adapter sees it. `body` is markdown with our marker
    stripped; `body_raw` is whatever the provider actually stores (ADF for Jira),
    kept so a provider-specific construct we cannot represent is never lost."""
    remote_id: str
    key: str                      # what a human calls it: "#12", "BM-17"
    url: str
    title: str
    body: str
    state: str                    # "open" | "closed"
    labels: list[str] = field(default_factory=list)
    assignee: str | None = None
    updated: str = ""
    closed_reason: str | None = None
    body_raw: object | None = None
    workshop_id: str | None = None  # from our marker, when present


@dataclass
class Filter:
    """What counts as workshop work in the tracker. `label` is the default because
    it is the one selection mechanism every provider has."""
    kind: str = "label"           # label | query | all
    label: str = "workshop"
    query: str = ""


class Tracker(Protocol):
    provider: str

    def whoami(self) -> Account: ...

    def list_since(self, cursor: str | None,
                   filt: Filter) -> tuple[list[RemoteIssue], str]: ...

    def get(self, remote_id: str) -> RemoteIssue: ...

    def create(self, *, title: str, body: str, labels: list[str],
               idem: str) -> RemoteIssue: ...

    def update(self, remote_id: str, patch: dict) -> RemoteIssue: ...

    def transition(self, remote_id: str, status: str) -> RemoteIssue: ...

    def comment(self, remote_id: str, markdown: str) -> None: ...

    def labels(self) -> list[str]: ...


def for_binding(binding: dict) -> Tracker:
    """The adapter for a binding. One place that knows the provider names, so
    adding a provider is a new module plus a row here."""
    provider = str(binding.get("provider") or "").strip()
    if provider == "github":
        from services.trackers import github as gh_adapter
        return gh_adapter.GitHubTracker(binding)
    raise ValueError(f"no tracker adapter for provider: {provider!r}")


PROVIDERS = ("github",)  # linear and jira are designed; neither is built
