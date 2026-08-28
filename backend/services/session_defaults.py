"""Step B8, first half: which branch a session is in, and what happens when it
first writes.

`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` §10.

    1. Opened from a ticket, run or branch -> that one. Inherited, shown,
       changeable.
    2. Otherwise -> the app's default ref, READ-ONLY (§4), with a staleness
       banner. Most sessions never leave this state.
    3. On first write (an EROFS, §4) -> create a NEW branch by default. Joining
       an existing one is offered, not automatic.
    4. Writing with no ticket is common and not second-class: it gets a branch a
       ticket can adopt later.

    So "which branch does a new chat go in" is never asked.

That last line is the acceptance test and it is a test in
`tests/test_session_defaults.py`, not a sentiment: `resolve()` always returns a
ref and never returns a question. `prompt_required` exists on the result purely
so that promise is assertable rather than implied by the absence of a field.

--------------------------------------------------------------------------
WHY DEFAULTING TO A NEW BRANCH IS THE WHOLE OF §10.3
--------------------------------------------------------------------------
Revision 1 of the design defaulted a first write into whatever branch was
already open. A branch MERGES AS ONE UNIT, so that silently couples work nobody
connected to another ticket's fate: the unrelated change rides along with an
accept, or is held hostage by a rejection, and neither is visible at the moment
it happens. Branches cost 0.27s (branches doc §13.2), which is exactly what
makes defaulting to a new one affordable rather than merely correct.

So `first_write_offer` puts `new_branch` in `default` unconditionally -- even
when `existing_branches` is full -- and joining is reachable ONLY through
`choose(..., "join", branch=...)`, which requires a branch that was offered.
`joined_automatically` is on the result and is always False; it is there to be
asserted against.

--------------------------------------------------------------------------
THIS MODULE NAMES NOTHING
--------------------------------------------------------------------------
It returns `ticket_id` and, when there is no ticket, an opaque `work_id`. It
does NOT build a branch name: §13.2 of the design puts naming and path policy in
`services/worktrees.py` (`_kebab_branch_slug`, `_branch_name`) and says no
service imports another's policy. A second slugifier here would be the same
branch name derived two ways, which is the derived-duplicate defect this repo
keeps removing.

Pure functions only: no database, no git, no clock beyond `uuid4`. That is what
lets §10 be tested as the resolution table it is.
"""
from __future__ import annotations

import uuid

__all__ = [
    "REF_SOURCES", "READ_ONLY_DETAIL", "NEW_BRANCH_DETAIL", "JOIN_DETAIL",
    "resolve", "first_write_offer", "choose",
]

# Where a session's ref came from. `app_default` is §10.2, the read-only case;
# the other three are §10.1, all of which are inherited from something the user
# picked.
REF_SOURCES = ("ticket", "run", "branch", "app_default")

_INHERITED_SOURCES = ("ticket", "run", "branch")

READ_ONLY_DETAIL = (
    "Reading {ref}. It is shared, so it is read-only here until you write. "
    "The first write gets its own branch.")
NEW_BRANCH_DETAIL = (
    "This write needs a branch of its own. A new one off {ref} keeps it "
    "separate; a branch merges as one unit, so joining another means "
    "accepting or dropping both together.")
JOIN_DETAIL = "Adding this work to {branch}. It will merge when {branch} does."


def resolve(*, opened_from: dict | None = None,
            app_default_ref: str = "main") -> dict:
    """§10.1 and §10.2: the ref this session is in, and whether it may write.

    `opened_from` is what the session was opened FROM, when it was opened from
    anything: `{"kind": "ticket" | "run" | "branch", "ref": ..., "ticket_id":
    ...}`. Anything else -- None, an unknown kind, a kind with no ref -- is
    §10.2, the app's default ref, read-only, with a staleness banner.

    `read_only` HERE IS THE DEFAULT, NOT THE ENFORCEMENT. The enforcement is the
    mount (§4, `services/tree_access.py`), and it asks the lease, not this
    function. What this answers is which ref a session starts in and whether the
    UI should say so; a session on an inherited ref still has to hold the lease
    to get a read-write mount, and this returning `read_only=False` never grants
    anything.
    """
    kind = (opened_from or {}).get("kind")
    ref = (opened_from or {}).get("ref")
    if kind in _INHERITED_SOURCES and ref:
        return {
            "ref": str(ref),
            "source": kind,
            "inherited": True,
            "shown": True,
            "changeable": True,
            "read_only": False,
            "staleness_banner": False,
            "ticket_id": (opened_from or {}).get("ticket_id"),
            "prompt_required": False,
            "detail": "",
        }
    return {
        "ref": str(app_default_ref),
        "source": "app_default",
        "inherited": False,
        # SHOWN AND CHANGEABLE EVEN HERE. §10's closing line is that the
        # question is never ASKED, not that the answer is hidden: the ref pill
        # shows what was picked and lets you change it.
        "shown": True,
        "changeable": True,
        # §10.2, and the reason it is the default rather than a precaution: the
        # app's default ref is the most-written tree in the system.
        "read_only": True,
        "staleness_banner": True,
        "ticket_id": (opened_from or {}).get("ticket_id"),
        "prompt_required": False,
        "detail": READ_ONLY_DETAIL.format(ref=app_default_ref),
    }


def first_write_offer(*, current_ref: str, ticket_id: str | None = None,
                      existing_branches: tuple[str, ...] | list[str] = (),
                      work_id: str | None = None) -> dict:
    """§10.3 and §10.4: what to offer when a write hits a read-only tree.

    The default is always a new branch. `options` carries the existing branches
    so joining is REACHABLE, and `choose` is the only way to take one.

    §10.4 IS A SHAPE, NOT A DISCLAIMER. A write with no ticket gets exactly the
    same offer as a write with one: same default, same options, same copy. The
    only difference is that it carries an opaque `work_id` instead of a ticket
    id and is marked `adoptable`, which is the doc's "a branch a ticket can
    adopt later" and is the reason nothing here is labelled scratch, temporary
    or untracked.
    """
    branches = [str(b) for b in (existing_branches or []) if b]
    return {
        "default": {
            "action": "new_branch",
            "base_ref": str(current_ref),
            "ticket_id": ticket_id,
            # No ticket: an id the branch layer can name a branch after and a
            # ticket can adopt later (§10.4).
            "work_id": (work_id or uuid.uuid4().hex[:8]) if not ticket_id else None,
            "adoptable": ticket_id is None,
            "detail": NEW_BRANCH_DETAIL.format(ref=current_ref),
        },
        "options": [
            {"action": "join", "branch": b,
             "detail": JOIN_DETAIL.format(branch=b)}
            for b in branches
        ],
        # Both always False, both asserted in the tests: this offer is never
        # taken on the session's behalf, and joining is never what happens when
        # nobody chose.
        "automatic": False,
        "joined_automatically": False,
        "current_ref": str(current_ref),
    }


def choose(offer: dict, action: str, *, branch: str | None = None) -> dict:
    """Take one of the offered actions. The only way to join a branch.

    Raises `ValueError` for a branch that was not offered, rather than creating
    the association anyway: an offer is a closed set, and a join that was never
    shown to anyone is the automatic coupling §10.3 forbids arriving by a
    different door.
    """
    if action == "new_branch":
        out = dict(offer["default"])
        out["joined_automatically"] = False
        return out
    if action != "join":
        raise ValueError(f"unknown action {action!r}; one of new_branch, join")
    for option in offer.get("options") or []:
        if option.get("branch") == branch:
            out = dict(option)
            out["base_ref"] = offer.get("current_ref")
            out["joined_automatically"] = False
            return out
    raise ValueError(f"{branch!r} was not one of the offered branches")
