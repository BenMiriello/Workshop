"""Commute service — data + scoring + alerting for benmiriello's daily
Mountain View ↔ San Francisco commute. See ~/workshop/commute/README.md for
the schedule/legs/targets data shape and notification policy.

Public surface:
    config.load_*()         — read schedule/legs/targets/sources from disk
    poller.poll_all()       — fetch every registered source and write to state/
    scoring.evaluate()      — produce a brief (target, current_leg, eta, late_risk)
    alerter.maybe_alert()   — apply policy (late_risk only, cooldown, etc.)
    llm.decide_interject()  — privacy-relay-routed decision: should we DM?
    timefmt.local_*         — US 12-hour formatting in operator's tz

Everything is read fresh from disk on each call — no caches across requests.
"""
from . import config, poller, scoring, alerter, llm, timefmt  # re-exports

__all__ = ["config", "poller", "scoring", "alerter", "llm", "timefmt"]
