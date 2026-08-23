"""Per-scope merge policy: may this scope's work land without a person?

Step C7 of `docs/platform-redesign/changes-and-review-2026-08-22.md`, section 6.
This is the per-SCOPE half of auto-merge; the per-CHANGE half is the `auto_merge`
flag on the change record, and `services/auto_merge.py` is what reads both.

WHERE IT LIVES, AND WHY BESIDE `scope_execution`. The design doc answers the
operator's own question -- ticket, agent or chat? -- with: the scope, with a
per-change override. A scope already carries `execution` (reactive / supervised
/ autonomous) and merge policy is the next notch on that same dial. On an agent
it would mean the same work is reviewed differently depending on who picked it
up, which is the wrong invariant; on a chat it would be a property of a
disposable session. So this module is `scope_execution`'s twin: same key (the
scope, never a path), same JSON-under-`.runtime` store, same "absence means the
safe answer" default, same drop-the-key-on-default write.

    manual                        DEFAULT. Nothing in this scope auto-merges on
                                  the scope's authority. A change may still
                                  carry its own `auto_merge` flag, because that
                                  is a person pre-approving a diff they read.
    auto_when_green               every non-advisory gate passing is enough.
    auto_when_green_and_low_risk  green AND `auto_merge.is_low_risk`.

THE DEFAULT IS OFF AND IT IS OFF EVERYWHERE. Nothing in this tree sets a
non-default policy, no scope adopts one, and no config ships one. That is
deliberate: section 8 of the design doc records an OPEN question the operator
owns -- whether `auto_when_green_and_low_risk` should exist at all in a
single-operator system, since it is the one place in the design where the
system merges code nobody looked at. A fail-closed default is not an answer to
that question; it is what lets the question stay open safely. Turning it on is a
deliberate act by the operator, through
`POST /agents/autonomy/merge-policy`, and nothing else.

FAIL CLOSED ON EVERY READ. An absent file, unreadable file, malformed JSON,
unknown value, non-string value: every one of them answers `manual`. A merge
that happened because a settings file could not be parsed is the worst failure
this feature can have, so the read never propagates an error into a decision --
it answers the safe thing and says so in the log.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

POLICIES = ("manual", "auto_when_green", "auto_when_green_and_low_risk")
DEFAULT_POLICY = "manual"

__all__ = ["POLICIES", "DEFAULT_POLICY", "get_policy", "set_policy",
           "allows_auto_merge", "requires_low_risk", "configured_scopes"]


def _store_path() -> Path:
    # Mirrors scope_execution: WORKSHOP_ROOT/.runtime/<file>. WORKSHOP_ROOT is
    # remapped to the mountpoint inside a confined turn (scoped_exec.turn_env),
    # so this resolves to the real tree whether confined or not.
    root = Path(os.environ.get(
        "WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))
    return root / ".runtime" / "merge-policy.json"


def _read() -> dict[str, Any]:
    """The store, or an EMPTY dict for any reason at all.

    Unlike `scope_execution._read` this catches every OSError and every decode
    failure rather than the two expected ones. The difference is what the two
    settings cost when they are wrong: an unreadable execution store means work
    does not run, and an unreadable merge store, if it were allowed to raise
    into the caller, would mean an exception on a path whose recovery is
    somebody's `except`. Empty means every scope is `manual`, which is the
    answer that merges nothing.
    """
    p = _store_path()
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("merge_policy: %s is unreadable (%s); every scope reads "
                    "as %s", p, e, DEFAULT_POLICY)
        return {}
    if not isinstance(data, dict):
        log.warning("merge_policy: %s is not an object; every scope reads as "
                    "%s", p, DEFAULT_POLICY)
        return {}
    return data


def _write(data: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, p)  # atomic


def get_policy(scope: str) -> str:
    """The scope's merge policy, DEFAULT manual (fail-closed) when unset.

    An unknown or non-string stored value is `manual` too, and says so: a value
    this module does not recognise is not a licence to guess which way the
    operator meant it.
    """
    if not scope:
        return DEFAULT_POLICY
    value = _read().get(scope)
    if value in POLICIES:
        return str(value)
    if value is not None:
        log.warning("merge_policy: scope %s holds an unknown policy %r; "
                    "reading as %s", scope, value, DEFAULT_POLICY)
    return DEFAULT_POLICY


def set_policy(scope: str, policy: str) -> str:
    if not scope:
        raise ValueError("a merge policy belongs to a scope; none was named")
    if policy not in POLICIES:
        raise ValueError(f"unknown merge policy {policy!r}; one of {POLICIES}")
    data = _read()
    if policy == DEFAULT_POLICY:
        # Storing the default is noise: absence already means manual. Drop the
        # key so the file stays a list of the scopes the operator deliberately
        # opened up, which is the list he will want to audit.
        data.pop(scope, None)
    else:
        data[scope] = policy
    _write(data)
    return policy


def allows_auto_merge(scope: str) -> bool:
    """True when this scope's policy lets a change merge on the SCOPE's
    authority (as opposed to a per-change pre-approval)."""
    return get_policy(scope) != DEFAULT_POLICY


def requires_low_risk(scope: str) -> bool:
    """True when this scope's policy additionally demands the low-risk
    classifier. False for `manual` too, which is not a licence -- callers must
    ask `allows_auto_merge` first."""
    return get_policy(scope) == "auto_when_green_and_low_risk"


def configured_scopes() -> list[tuple[str, str]]:
    """Every scope the operator has deliberately opened up, as (scope, policy).

    The store only ever holds non-default entries (`set_policy` drops a key set
    back to manual), so this is exactly that set with no filtering needed. It
    is the audit answer to "what can land without me".
    """
    return [(k, v) for k, v in sorted(_read().items())
            if v in POLICIES and v != DEFAULT_POLICY]
