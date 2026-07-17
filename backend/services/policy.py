"""Store access policy — the operator's programmatic ceiling on a Store URI.

A policy record answers "what is the MOST any background process could ever get
out of this Store", declared by the operator, in code-checkable fields. It is the
thing invariant I1 is enforced against: the mediated broker (services/broker.py)
may only ever hand back something INSIDE this record, so a broker whose reasoning
is fully captured by an injected `purpose` string still cannot exceed a maximum
the operator already blessed.

Three tiers (docs/platform-redesign/resource-access-protocol-2026-07-16.md Part 4):

    open      any caller holding a grant that reaches this URI may act within its
              verbs. Nobody decides anything at request time. Zero cost.
    gated     mechanical only: verbs + subtree + the grant's filter caveat. Code
              decides, from this record. Zero cost.
    mediated  no access without a grant, and a smart LLM adjudicates the first
              crossing to mint one. One Sonnet call per grant, not per read.

`open` IS THE DEFAULT for a Store with no policy record, and that is load-bearing:
an empty (or absent) store-policies.json means exactly today's behavior. A fresh
clone gates nothing. Policy is opt-in.

WHERE THIS LIVES, AND WHY NOT IN THE FOLDER. `backend/store-policies.json`, beside
`resource-grants.json`, OUTSIDE every store it protects. A `.access.json` sidecar
inside the folder would be lovely and portable and is rejected: `write` on a notes
folder is the single most ordinary grant we will ever mint, and a sidecar makes the
gate writable by the party it gates. Policy that lives inside the resource it
protects is not policy.

TWO FAILURE DIRECTIONS, ON PURPOSE:

  * fail-OPEN on a corrupt/unreadable policy FILE (`load_policies`). A JSON syntax
    error must never lock the operator out of their own notes. Corruption means
    "no policy", which means `open`, which means today's behavior.
  * fail-CLOSED on a well-formed file containing an ILLEGAL RECORD (PolicyError).
    That is operator intent we can read and cannot honor (e.g. `mediated` on a
    Secret), and silently downgrading it to `open` would turn a typo into a leak.
    It raises, loudly, at load.
  * fail-CLOSED in the broker itself (no adjudicator -> deny). See broker.py.

A Store of type "Secret" may not be `mediated`. There is no filtered view of a
secret: any release is total release, so "release only the relevant part of the
API key" is a category error. Secrets are `gated`. Naming a place the flagship
feature is banned is how we know the tiers mean something.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services import permissions, stores

# Operator-owned, backend-managed — same posture as grants.json / resource-grants.json.
POLICY_PATH = Path(os.environ.get(
    "WORKSHOP_STORE_POLICIES_PATH",
    str(Path(__file__).resolve().parent.parent / "store-policies.json"),
))

TIERS = ("open", "gated", "mediated")


class PolicyError(ValueError):
    """A policy record the operator wrote that the schema refuses to honor."""


# --------------------------------------------------------------------------- #
# Response modes: a registry, not an enum (A8's "or other" is a real requirement).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResponseMode:
    """One way a mediated answer may be shaped.

    `generative` is the gate that matters: a generative mode emits novel tokens,
    so its output is NOT a subset of its input and no mechanical verifier of it
    exists. Adding a generative mode later inherits the opt-in requirement here
    without anyone having to remember to apply it.
    """
    name: str
    generative: bool


# The seed set. `summarized` is deliberately ABSENT rather than listed-and-disabled:
# it is the one mode with no possible mechanical verifier (you cannot assert that a
# summary of a note did not reveal, or invent, something), and a name in this dict
# is a promise that the provider can execute it.
RESPONSE_MODES: dict[str, ResponseMode] = {
    "verbatim": ResponseMode("verbatim", generative=False),
    "filtered": ResponseMode("filtered", generative=False),
    "redacted": ResponseMode("redacted", generative=False),
}

# Adjudicator floor (Part 6). Ollama is absent by construction, not by a check:
# its availability is a function of GPU contention, and a judge that flips with
# GPU load is not a judge. Haiku is absent too — the call happens once per grant,
# amortized over grant_ttl_s, so the cheap-model argument buys almost nothing and
# costs judgment under adversarial input.
ADJUDICATOR_TIERS = ("sonnet", "opus")


@dataclass(frozen=True)
class StorePolicy:
    """The operator's declared maximum for a Store URI (or its subtree).

    Programmatic fields (`verbs_max`, `topics_allow`, `topics_deny`,
    `requesters_allow`, `response_modes`, `fields_max`) are enforced in code after
    the broker returns. `rules_text` is prose handed to the broker as guidance.
    The asymmetry IS invariant I1: prose can only ever influence NARROWING, because
    widening is checked against the programmatic fields by code the model never
    touches. Sloppy prose is a usability problem, never a hole.
    """
    store_uri: str
    tier: str = "open"
    subtree: bool = True
    verbs_max: tuple[str, ...] = ()
    topics_allow: tuple[str, ...] = ()
    topics_deny: tuple[str, ...] = ()
    requesters_allow: tuple[str, ...] = ("agent:*",)
    response_modes: tuple[str, ...] = ("filtered",)
    default_response_mode: str = "filtered"
    # The field projection a non-`read` verb may ever return (the Finance case:
    # search may hand back date/amount/merchant, never the account id or balance).
    # Empty means "the adapter's natural shape"; it is a ceiling, not a request.
    fields_max: tuple[str, ...] = ()
    grant_ttl_s: int = 900
    rules_text: str = ""
    adjudicator_min_tier: str = "sonnet"

    def permits_requester(self, subject: str) -> bool:
        """Does `requesters_allow` cover this grant subject ("agent:notes-triage")?
        Supports an exact match and a trailing "*" wildcard ("agent:*")."""
        for pattern in self.requesters_allow:
            if pattern == "*" or pattern == subject:
                return True
            if pattern.endswith("*") and subject.startswith(pattern[:-1]):
                return True
        return False

    def covers(self, uri: str) -> bool:
        """Is `uri` inside this record's reach? Same rule permissions.check uses."""
        if uri == self.store_uri:
            return True
        return self.subtree and uri.startswith(self.store_uri.rstrip("/") + "/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_uri": self.store_uri,
            "tier": self.tier,
            "subtree": self.subtree,
            "verbs_max": list(self.verbs_max),
            "topics_allow": list(self.topics_allow),
            "topics_deny": list(self.topics_deny),
            "requesters_allow": list(self.requesters_allow),
            "response_modes": list(self.response_modes),
            "default_response_mode": self.default_response_mode,
            "fields_max": list(self.fields_max),
            "grant_ttl_s": self.grant_ttl_s,
            "rules_text": self.rules_text,
            "adjudicator_min_tier": self.adjudicator_min_tier,
        }


def open_policy(store_uri: str) -> StorePolicy:
    """The policy of a Store nobody wrote a record for: `open`, full verb set.

    This is what "an empty policy file means today's behavior" is made of."""
    return StorePolicy(
        store_uri=store_uri,
        tier="open",
        subtree=True,
        verbs_max=tuple(permissions.VERBS),
        response_modes=("verbatim",),
        default_response_mode="verbatim",
        requesters_allow=("*",),
    )


# --------------------------------------------------------------------------- #
# Loading + validation.
# --------------------------------------------------------------------------- #
def _strs(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise PolicyError(f"{field_name} must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def _store_type(store_uri: str) -> str | None:
    """The declared type of the Store this record names, or None if the URI names
    no registered Store (a policy may legitimately precede its Store)."""
    store = stores.get(store_uri)
    return store.type if store is not None else None


def coerce_policy(row: Any) -> StorePolicy:
    """Validate one raw record into a StorePolicy, or raise PolicyError.

    This is the fail-CLOSED half: a record the operator wrote and we can read but
    cannot honor is an error, never a silent downgrade to `open`."""
    if not isinstance(row, dict):
        raise PolicyError("a policy record must be an object")
    uri = row.get("store_uri") or row.get("uri")
    if not isinstance(uri, str) or not uri:
        raise PolicyError("a policy record needs a store_uri")
    tier = str(row.get("tier") or "open")
    if tier not in TIERS:
        raise PolicyError(f"{uri}: unknown tier {tier!r} (one of {', '.join(TIERS)})")

    # There is no filtered view of a secret; any release is total release.
    if tier == "mediated" and _store_type(uri) == "Secret":
        raise PolicyError(
            f"{uri}: tier 'mediated' is refused on a Store of type Secret — a secret "
            "is atomic, so there is no 'relevant part' for a broker to release. Use "
            "tier 'gated'."
        )

    verbs_max = _strs(row.get("verbs_max"), "verbs_max") or tuple(permissions.VERBS)
    unknown = [v for v in verbs_max if v not in permissions.VERBS]
    if unknown:
        raise PolicyError(
            f"{uri}: unknown verbs {unknown} (one of {', '.join(permissions.VERBS)})"
        )

    modes = _strs(row.get("response_modes"), "response_modes") or ("verbatim",)
    for m in modes:
        if m not in RESPONSE_MODES:
            extra = (
                " `summarized` has no mechanical verifier (its output is not a subset "
                "of its input) and is deliberately not implemented."
                if m == "summarized" else ""
            )
            raise PolicyError(
                f"{uri}: unknown response mode {m!r} "
                f"(one of {', '.join(sorted(RESPONSE_MODES))})." + extra
            )
    default_mode = str(row.get("default_response_mode") or modes[0])
    if default_mode not in modes:
        raise PolicyError(
            f"{uri}: default_response_mode {default_mode!r} is not in response_modes"
        )

    tier_name = str(row.get("adjudicator_min_tier") or "sonnet").lower()
    if tier_name not in ADJUDICATOR_TIERS:
        raise PolicyError(
            f"{uri}: adjudicator_min_tier {tier_name!r} is below the floor "
            f"(one of {', '.join(ADJUDICATOR_TIERS)}); Ollama and Haiku are excluded "
            "from adjudication entirely."
        )

    ttl = row.get("grant_ttl_s", 900)
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
        raise PolicyError(f"{uri}: grant_ttl_s must be a positive integer")

    return StorePolicy(
        store_uri=uri,
        tier=tier,
        subtree=bool(row.get("subtree", True)),
        verbs_max=verbs_max,
        topics_allow=_strs(row.get("topics_allow"), "topics_allow"),
        topics_deny=_strs(row.get("topics_deny"), "topics_deny"),
        requesters_allow=_strs(row.get("requesters_allow"), "requesters_allow") or ("agent:*",),
        response_modes=modes,
        default_response_mode=default_mode,
        fields_max=_strs(row.get("fields_max"), "fields_max"),
        grant_ttl_s=ttl,
        rules_text=str(row.get("rules_text") or ""),
        adjudicator_min_tier=tier_name,
    )


def load_policies() -> list[StorePolicy]:
    """Every declared policy record.

    FAIL-OPEN on a missing/unreadable/unparseable file: no policy means `open`
    means today's behavior. A corrupt JSON file locking the operator out of their
    own notes would be the worst possible reading of "secure".

    Raises PolicyError on a well-formed file with an illegal record — see the
    module docstring for why those two directions differ."""
    if not POLICY_PATH.exists():
        return []
    try:
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("policies") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [coerce_policy(row) for row in rows]


def for_uri(store_uri: str) -> StorePolicy:
    """The policy governing a URI: longest-prefix match, else `open`.

    Longest-prefix gives per-folder granularity and inheritance for free — a
    record on `.../project-acme` covers every subfolder until a longer record
    overrides it. (`manifests.resolve_app_for_path` is the existing precedent.)"""
    best: StorePolicy | None = None
    for pol in load_policies():
        if not pol.covers(store_uri):
            continue
        if best is None or len(pol.store_uri) > len(best.store_uri):
            best = pol
    return best or open_policy(store_uri)
