"""What THIS installation is, DECLARED rather than written into prompt text.

The four-layer prompt contract (docs/platform-redesign/
agent-context-and-toolkit-2026-08-20.md) makes L0 (kernel), L1 (orientation) and
L2 (persona) PLATFORM text: every installation and every user receives them, so
they may carry only generalized product doctrine. A fact about one deployment is
not doctrine -- and until 2026-08-27 five of them were string literals in
`services/chat_context.py`: the vendor of the coding runner, the package
registries this host's egress allows, which networks are reachable, and the name
of the git forge the operator publishes to.

This module is the same move `services/app_persona.py` made for app assistants.
Every fact comes from the environment (~/.config/workshop/instance.env, the same
place WORKSHOP_GITHUB_OWNER and WORKSHOP_BLOCKED_REPO_OWNERS already live), never
from this file. Unset means the prompt says the generalized thing and names
nothing, which is correct for an installation that has not declared itself: a
prompt that guesses is worse than one that generalizes.
"""
from __future__ import annotations

import os


def _csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name) or ""
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def runner_label() -> str:
    """The coding runner that actually executes an agentic turn, e.g. a CLI's
    product name. Empty when this installation has not declared one."""
    return os.environ.get("WORKSHOP_RUNNER_LABEL", "").strip()


def runner_session() -> str:
    """"a <runner> session", for a briefing that tells an agent what it IS."""
    label = runner_label()
    return f"a {label} session" if label else "an agentic coding session"


def runner_grant(noun: str = "toolset") -> str:
    """"full <runner> toolset" / "full agentic tool access" -- the phrase a
    persona uses for the whole-machine grant a maker gets."""
    label = runner_label()
    return f"full {label} {noun}" if label else f"full agentic {noun}"


def egress_allowed_hosts() -> tuple[str, ...]:
    """Package/source hosts a build turn may fetch from, as this host's egress
    policy actually allows them. Empty means the prompt does not enumerate."""
    return _csv("WORKSHOP_EGRESS_ALLOWED_HOSTS")


def reachable_networks() -> tuple[str, ...]:
    """Networks always reachable from a turn. Loopback is true everywhere; a
    private overlay or LAN is this installation's own fact and must be declared."""
    return _csv("WORKSHOP_REACHABLE_NETWORKS") or ("loopback (127.0.0.1)",)


def git_forge() -> str:
    """The git hosting vendor this installation publishes to. Empty when
    undeclared: the branching rule then says "remote" and names nobody."""
    return os.environ.get("WORKSHOP_GIT_FORGE", "").strip()
