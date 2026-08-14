"""The one mapping between the JSON store's vocabulary and the schema's.

THREE COPIES OF THIS WOULD BE THE EXACT DRIFT THIS PROJECT EXISTS TO REMOVE.
The backfill needs it to load history, `repo.py` needs it to hand records back
in the legacy shape, and `ticket_sync.py` needs it to mirror live writes. When
they disagree, a ticket changes status by being read and written back, which is
a corruption with no error message. So it lives here and they import it.

Migration 769d64dbfa74 (priority bands) and migration 966ddcaa35ae (the seeded
states and types) must agree with this file. `tests/test_db_schema.py` asserts
the priority bands match; the states are asserted by the backfill's own fidelity
check, which compares every status count against the source.
"""
from __future__ import annotations

__all__ = ["STATES", "TYPES", "STATUS_TO_STATE", "STATE_TO_STATUS",
           "priority_band", "PRIORITY_BANDS"]

# The eight statuses the JSON store hard-coded, as (token, name, category,
# position). Category is the fixed axis; the name is what a human reads and what
# a tenant may rename. `parked` keeps its dead-letter meaning because that is
# what the code did, whatever the entity model said.
STATES = [
    ("draft",     "Draft",       "backlog",   1.0),
    ("todo",      "Todo",        "unstarted", 2.0),
    ("doing",     "In Progress", "started",   3.0),
    ("in_review", "In Review",   "started",   4.0),
    ("blocked",   "Blocked",     "started",   5.0),
    ("parked",    "Parked",      "backlog",   6.0),
    ("done",      "Done",        "completed", 7.0),
    ("cancelled", "Cancelled",   "canceled",  8.0),
]

# `execution` becomes a type, because "what kind of work is this" is the concept
# every tracker already has. `agent_executable` is the one behaviour the old
# field actually drove.
TYPES = [
    ("code",      "Code",      True,  "A change a worker can make and a gate can check."),
    ("judgement", "Judgement", False, "Needs a decision, not a diff."),
    ("external",  "External",  False, "Happens outside this system (host admin, a vendor)."),
]

STATUS_TO_STATE = {token: name for token, name, _, _ in STATES}
STATE_TO_STATUS = {name: token for token, name, _, _ in STATES}

# The JSON store held a signed integer. Bands chosen from the live distribution
# (38 distinct values across 0..140), matching migration 769d64dbfa74 exactly so
# a fresh backfill and a migrated database agree.
PRIORITY_BANDS = ((100, "urgent"), (80, "high"), (40, "medium"), (1, "low"))


def priority_band(value) -> str:
    """A legacy integer priority as its band. Idempotent on a band name, so a
    record that has already been through the schema round-trips unchanged."""
    if isinstance(value, str) and value in {b for _, b in PRIORITY_BANDS} | {"none"}:
        return value
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return "none"
    for floor, band in PRIORITY_BANDS:
        if n >= floor:
            return band
    return "none"
