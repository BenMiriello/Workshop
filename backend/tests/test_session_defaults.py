"""[B8] §10's four defaults, as a resolution table.

`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` §10. The
section's closing line is the acceptance test and it is asserted literally
below: "which branch does a new chat go in" is never asked.
"""
from __future__ import annotations

import pytest

from services import session_defaults as sd


class TestCase1OpenedFromSomething:
    """§10.1: opened from a ticket, run or branch -> that one. Inherited,
    shown, changeable."""

    @pytest.mark.parametrize("kind", ["ticket", "run", "branch"])
    def test_it_takes_the_ref_it_was_opened_from(self, kind):
        r = sd.resolve(opened_from={"kind": kind, "ref": "wt/app/fix-login"},
                       app_default_ref="main")
        assert r["ref"] == "wt/app/fix-login"
        assert r["source"] == kind
        assert r["inherited"] is True
        assert r["shown"] is True
        assert r["changeable"] is True
        assert r["read_only"] is False
        assert r["staleness_banner"] is False

    def test_the_ticket_rides_along(self):
        r = sd.resolve(opened_from={"kind": "ticket", "ref": "wt/app/t-9",
                                    "ticket_id": "t-9"})
        assert r["ticket_id"] == "t-9"

    def test_a_kind_with_no_ref_falls_through_to_the_default(self):
        # Half a binding is not a binding: a session cannot be put in a branch
        # whose name nobody supplied.
        r = sd.resolve(opened_from={"kind": "ticket"}, app_default_ref="main")
        assert r["source"] == "app_default" and r["ref"] == "main"

    def test_an_unknown_kind_falls_through_to_the_default(self):
        r = sd.resolve(opened_from={"kind": "spreadsheet", "ref": "x"},
                       app_default_ref="main")
        assert r["source"] == "app_default"


class TestCase2OtherwiseTheDefaultRefReadOnly:
    """§10.2: otherwise the app's default ref, READ-ONLY, with a staleness
    banner. Most sessions never leave this state."""

    def test_a_new_chat_lands_on_the_default_ref_read_only(self):
        r = sd.resolve(app_default_ref="main")
        assert r["ref"] == "main"
        assert r["source"] == "app_default"
        assert r["inherited"] is False
        assert r["read_only"] is True
        assert r["staleness_banner"] is True

    def test_it_is_still_shown_and_still_changeable(self):
        # §10's promise is that the question is never ASKED, not that the answer
        # is hidden: the ref pill shows what was picked and lets you change it.
        r = sd.resolve()
        assert r["shown"] is True and r["changeable"] is True

    def test_the_copy_names_the_ref_and_says_why(self):
        r = sd.resolve(app_default_ref="trunk")
        assert "trunk" in r["detail"] and "read-only" in r["detail"]


class TestCase3OnFirstWriteANewBranch:
    """§10.3: create a NEW branch by default. Joining an existing one is
    offered, not automatic."""

    def test_the_default_is_a_new_branch(self):
        offer = sd.first_write_offer(current_ref="main")
        assert offer["default"]["action"] == "new_branch"
        assert offer["default"]["base_ref"] == "main"

    def test_it_is_still_a_new_branch_when_branches_exist_to_join(self):
        # Revision 1 defaulted to joining. A branch merges as one unit, so that
        # silently couples unrelated work to another ticket's fate.
        offer = sd.first_write_offer(current_ref="main",
                                     existing_branches=["wt/a/one", "wt/a/two"])
        assert offer["default"]["action"] == "new_branch"
        assert [o["branch"] for o in offer["options"]] == ["wt/a/one", "wt/a/two"]

    def test_nothing_is_automatic(self):
        offer = sd.first_write_offer(current_ref="main",
                                     existing_branches=["wt/a/one"])
        assert offer["automatic"] is False
        assert offer["joined_automatically"] is False

    def test_joining_requires_choosing_it(self):
        offer = sd.first_write_offer(current_ref="main",
                                     existing_branches=["wt/a/one"])
        joined = sd.choose(offer, "join", branch="wt/a/one")
        assert joined["action"] == "join"
        assert joined["branch"] == "wt/a/one"
        assert joined["joined_automatically"] is False

    def test_joining_a_branch_that_was_not_offered_is_refused(self):
        offer = sd.first_write_offer(current_ref="main",
                                     existing_branches=["wt/a/one"])
        with pytest.raises(ValueError):
            sd.choose(offer, "join", branch="wt/a/somewhere-else")

    def test_an_unknown_action_is_refused(self):
        offer = sd.first_write_offer(current_ref="main")
        with pytest.raises(ValueError):
            sd.choose(offer, "merge")

    def test_choosing_the_default_needs_no_branch_name(self):
        offer = sd.first_write_offer(current_ref="main", ticket_id="t-3")
        chosen = sd.choose(offer, "new_branch")
        assert chosen["action"] == "new_branch" and chosen["ticket_id"] == "t-3"

    def test_this_module_names_nothing(self):
        # §13.2: naming and path policy live in services/worktrees.py, and a
        # second slugifier here would be one branch name derived two ways.
        offer = sd.first_write_offer(current_ref="main", ticket_id="t-3")
        assert "branch" not in offer["default"]
        assert "branch_slug" not in offer["default"]


class TestCase4WritingWithNoTicket:
    """§10.4: common, and NOT second-class -- it gets a branch a ticket can
    adopt later."""

    def test_it_gets_the_same_offer_shape(self):
        with_ticket = sd.first_write_offer(current_ref="main", ticket_id="t-1")
        without = sd.first_write_offer(current_ref="main")
        assert set(with_ticket) == set(without)
        assert with_ticket["default"]["action"] == without["default"]["action"]
        assert (with_ticket["default"]["detail"]
                == without["default"]["detail"])

    def test_it_carries_a_work_id_a_ticket_can_adopt(self):
        offer = sd.first_write_offer(current_ref="main")
        assert offer["default"]["ticket_id"] is None
        assert offer["default"]["work_id"]
        assert offer["default"]["adoptable"] is True

    def test_a_ticketed_write_needs_no_work_id(self):
        offer = sd.first_write_offer(current_ref="main", ticket_id="t-1")
        assert offer["default"]["work_id"] is None
        assert offer["default"]["adoptable"] is False

    def test_nothing_calls_it_scratch(self):
        offer = sd.first_write_offer(current_ref="main")
        blob = repr(offer).lower()
        for word in ("scratch", "temporary", "untracked", "informal"):
            assert word not in blob


def test_which_branch_does_a_new_chat_go_in_is_never_asked():
    """§10's closing line, as an assertion. Every reachable input resolves to a
    ref with no question attached."""
    inputs = [
        None,
        {},
        {"kind": "ticket", "ref": "wt/a/t-1"},
        {"kind": "run", "ref": "wt/a/run-2"},
        {"kind": "branch", "ref": "wt/a/manual"},
        {"kind": "ticket"},
        {"kind": "nonsense", "ref": "x"},
    ]
    for opened_from in inputs:
        r = sd.resolve(opened_from=opened_from, app_default_ref="main")
        assert r["ref"], opened_from
        assert r["prompt_required"] is False, opened_from
        assert "choices" not in r and "options" not in r, opened_from
