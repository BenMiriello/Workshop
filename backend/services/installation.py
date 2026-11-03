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


# STATUS, measured 2026-09-17: of these five declarations, only
# WORKSHOP_CANONICAL_ORIGIN is actually set anywhere. The other four are
# empty NOT because they are inapplicable to this installation -- an
# installation that runs an agentic coding CLI against a GitHub remote, behind
# some egress and network policy, is exactly the case these exist to name --
# but because populating them was never done after this module was created on
# 2026-08-27 to receive them. That is worth writing down explicitly, because
# "empty" alone does not say whether a value belongs here and is simply
# missing, or whether it does not apply. All four below are the former:
#
#   WORKSHOP_RUNNER_LABEL          not set yet. Knowable and low-risk to
#                                   declare (a product name, not a secret);
#                                   simply never written to instance.env.
#   WORKSHOP_GIT_FORGE             not set yet, same as above -- this
#                                   installation already has WORKSHOP_GITHUB_OWNER
#                                   set (github_identity.py), so the vendor is
#                                   already known, just not named here too.
#   WORKSHOP_EGRESS_ALLOWED_HOSTS  not set yet, and NOT something to fill from
#                                   a guess: it asserts what this host's actual
#                                   firewall/proxy egress policy allows, and a
#                                   wrong enumeration here is worse than the
#                                   generalized fallback because it would be
#                                   read as verified.
#   WORKSHOP_REACHABLE_NETWORKS    not set yet, same caution as the egress list
#                                   -- it is a claim about real network
#                                   topology (a tailnet, a LAN) that only the
#                                   operator can state correctly.
#
# None of the four is populated by this module or by any code path: they are
# declared with `~/.config/workshop/instance.env`, which is outside this repo
# and not writable from here. This file only reads them and falls back to a
# generalized phrase when they are absent, by design (see module docstring).
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


# --- where a browser reaches this installation ---------------------------
# ONE list, read in preference order. It was spelled out twice, in
# `services/operator_notify.py` and in `routes/voice_stream.py`, and a value
# spelled twice is a value that will be spelled wrong in one of them. Both call
# sites now read it from here.
#
# The order is a preference, not a fallback chain over failures: the canonical
# origin is the address the installation calls itself, the public one is the
# address it is reachable at from outside, and the local one is the address it
# answers on its own network. A link, a redirect or an allowed WebSocket origin
# is only worth anything if it resolves from wherever it is used, which is
# usually not this machine.

ORIGIN_ENVS = ("WORKSHOP_CANONICAL_ORIGIN", "WORKSHOP_PUBLIC_ORIGIN",
               "WORKSHOP_LOCAL_ORIGIN")


def declared_origins() -> tuple[str, ...]:
    """Every browser-reachable origin this installation declares, best first.

    Trailing slashes are stripped, so a caller may compare or concatenate
    without normalising again. Empty when the installation declares none, which
    is a real answer: a fresh clone has no address and anything built from a
    guess would point at another instance.
    """
    out: list[str] = []
    for var in ORIGIN_ENVS:
        value = (os.environ.get(var) or "").strip().rstrip("/")
        if value and value not in out:
            out.append(value)
    return tuple(out)


DEFAULT_DEV_PORT = 5173


def dev_port() -> int:
    """The port the Vite dev server listens on: WORKSHOP_VITE_PORT, the same
    variable scripts/frontends_watchdog.sh launches it with, else 5173."""
    raw = (os.environ.get("WORKSHOP_VITE_PORT") or "").strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_DEV_PORT
    return port if 0 < port < 65536 else DEFAULT_DEV_PORT


def dev_origins() -> tuple[str, ...]:
    """The loopback origins a browser uses to reach the dev server.

    The standard port is always included, so a checkout that has not declared a
    port still works; a declared WORKSHOP_VITE_PORT adds its own pair.
    """
    ports = [DEFAULT_DEV_PORT]
    if dev_port() not in ports:
        ports.append(dev_port())
    return tuple(f"http://{host}:{p}" for p in ports
                 for host in ("localhost", "127.0.0.1"))


def public_origin() -> str:
    """The address the operator opens this installation at, or empty.

    The first of `declared_origins()`. This is what a redirect URL, a deep link
    or an OAuth callback is built from.
    """
    origins = declared_origins()
    return origins[0] if origins else ""


def operator_dm_target() -> str:
    """The account the platform sends the operator a notification at.

    Declared with WORKSHOP_OPERATOR_DISCORD_USER: the operator's own id on the
    messaging surface this installation uses, which is a fact about one
    deployment and belongs here rather than in any prompt or any tracked module.

    Empty when undeclared, and that is a real answer rather than a gap to paper
    over: `services/operator_notify.configured()` reads it, the notify tool's
    grant is computed from that, and an installation with nowhere to send is one
    whose chats are never told they can reach anyone. A destination guessed from
    whoever happens to be around would deliver someone's work to a stranger.
    """
    return os.environ.get("WORKSHOP_OPERATOR_DISCORD_USER", "").strip()


def timezone_name() -> str:
    """The IANA timezone this installation keeps its wall clock in.

    Declared with WORKSHOP_TIMEZONE. Unset falls back to the host's own zone,
    which is the right default rather than a guess: a personal installation runs
    on a machine already set to the operator's time, and every scheduled thing on
    it (a wake-up, a report, a nightly sweep) is already expressed against that
    clock. Empty only when the host declares nothing either, in which case the
    caller states the offset and names no zone.
    """
    declared = os.environ.get("WORKSHOP_TIMEZONE", "").strip()
    if declared:
        return declared
    env_tz = os.environ.get("TZ", "").strip()
    if env_tz:
        return env_tz
    return _host_zone_name()


def _host_zone_name() -> str:
    """The host's configured zone, read where the OS keeps it. Never raises: an
    unreadable answer means the prompt states an offset and names no zone."""
    import os.path
    from pathlib import Path

    try:
        raw = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if raw:
            return raw
    except OSError:
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in link:
            return link.split(marker, 1)[1]
    except OSError:  # pragma: no cover - defensive
        pass
    return ""


def local_tzinfo():
    """A tzinfo for `timezone_name()`, or the host's current offset.

    The fallback is a fixed offset rather than UTC: showing an agent a UTC clock
    on a machine set to another zone is worse than showing it no zone name, and
    the offset is always knowable.
    """
    name = timezone_name()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - an unknown zone falls back, never raises
            pass
    import datetime as _dt

    return _dt.datetime.now().astimezone().tzinfo


def local_now():
    """Right now, in this installation's own timezone."""
    import datetime as _dt

    return _dt.datetime.now(tz=local_tzinfo())


# --- forge account policy ------------------------------------------------
# Which account this installation owns repos under, and which accounts it must
# refuse outright. Both are env declarations, like everything else here.

OWNER_ENV = "WORKSHOP_GITHUB_OWNER"
BLOCKED_OWNERS_ENV = "WORKSHOP_BLOCKED_REPO_OWNERS"


def owner() -> str:
    """The GitHub account this installation owns its repos under.

    Unset is an error rather than a guess: a repo created under the wrong
    account is not something to notice after the fact.
    """
    value = (os.environ.get(OWNER_ENV) or "").strip()
    if not value:
        raise ValueError(
            f"no GitHub owner configured: set {OWNER_ENV} in "
            "~/.config/workshop/instance.env to the account this install uses")
    return value


def blocked_owners() -> tuple[str, ...]:
    raw = os.environ.get(BLOCKED_OWNERS_ENV) or ""
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def reject_blocked(*strings: str | None) -> None:
    """Raise ValueError if any argument names an account this install must not touch.

    Substring match, case-insensitive, so naming an org also catches its repos
    and their remote URLs. An empty configuration blocks nothing.

    IT LIVES HERE BECAUSE IT IS AN INSTALLATION DECLARATION, not forge plumbing.
    It reads one env var and answers a policy question; it calls no API and
    knows nothing about git. While it sat in `services/github.py` it made a
    genuine import cycle: `github` builds its environment from
    `github_identity`, and `github_identity` has to ask this function whether a
    credential names a forbidden account, so the two modules imported each
    other and one direction had to be a function-local import to resolve at all.
    Moving the policy DOWN to the module that already owns "what is true of this
    installation" breaks the cycle without either side losing anything.
    """
    blocked = blocked_owners()
    if not blocked:
        return
    for s in strings:
        if not s:
            continue
        low = s.lower()
        for name in blocked:
            if name in low:
                raise ValueError(
                    f"{name!r} is out of scope for this installation "
                    f"({BLOCKED_OWNERS_ENV}): refusing owner/name/remote containing it")
