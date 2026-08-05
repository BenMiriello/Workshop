"""A role is the set of writes it permits, and the tools that follow from it.

Until this existed, `role` changed exactly one expression in the codebase and
reviewer, triager, keeper and broker were behaviourally identical to each other
and to no role at all. These tests pin the distinctions that make them different
kinds of thing rather than different labels.

Every assertion here is about COORDINATION, not confinement. The autonomy loop
runs as the same uid as everything else, so an agent could rewrite the very
definition granting it these permissions. That is SEC-A1 and it is open. What is
tested here is that the system's own mediated paths behave as declared.
"""
import pytest

from services import agent_authority as aa


class TestWriteSets:
    def test_maker_writes_the_tree(self):
        assert aa.may_write("maker", "tree") is True

    def test_reviewer_cannot_write_the_tree(self):
        """A reviewer that can edit is a second maker, and its verdict stops
        meaning anything the moment it can fix what it is judging."""
        assert aa.may_write("reviewer", "tree") is False
        assert aa.may_write("reviewer", "tasks") is False
        assert aa.may_write("reviewer", "verdict") is True

    def test_triager_routes_but_does_not_build(self):
        assert aa.may_write("triager", "tasks") is True
        assert aa.may_write("triager", "tree") is False

    def test_keeper_writes_memory_not_code(self):
        assert aa.may_write("keeper", "notes") is True
        assert aa.may_write("keeper", "learnings") is True
        assert aa.may_write("keeper", "tree") is False
        assert aa.may_write("keeper", "tasks") is False

    def test_broker_writes_nothing(self):
        for target in aa.WRITE_TARGETS:
            assert aa.may_write("broker", target) is False

    def test_no_role_writes_nothing(self):
        """Absent a declaration an agent is inert, never unrestricted."""
        for target in aa.WRITE_TARGETS:
            assert aa.may_write(None, target) is False

    def test_unknown_role_writes_nothing(self):
        assert aa.may_write("wizard", "tree") is False

    def test_unknown_target_is_denied(self):
        """A typo in a target name must not read as permission."""
        assert aa.may_write("maker", "evrything") is False

    def test_role_is_case_and_space_insensitive(self):
        assert aa.may_write(" Maker ", "tree") is True


class TestDerivedTools:
    def test_only_the_tree_writer_gets_write_tools(self):
        tools = aa.tools_for_role("maker")
        assert {"Write", "Edit", "MultiEdit", "Bash"} <= set(tools)
        for role in ("reviewer", "triager", "keeper"):
            assert not ({"Write", "Edit", "MultiEdit", "Bash"} & set(aa.tools_for_role(role)))

    def test_roles_that_write_something_can_read(self):
        """A reviewer must read the diff it judges; a triager the work it routes."""
        for role in ("maker", "reviewer", "triager", "keeper"):
            assert "Read" in aa.tools_for_role(role)

    def test_broker_gets_nothing(self):
        """Its contract is specifically that it does not read the data it
        adjudicates."""
        assert aa.tools_for_role("broker") == []

    def test_roleless_agent_is_inert(self):
        assert aa.tools_for_role(None) == []


class TestNarrowing:
    def test_a_definition_may_ask_for_less(self):
        assert aa.narrow_tools("maker", ["Read"]) == ["Read"]

    def test_a_definition_may_not_ask_for_more(self):
        """Least privilege with the role as the upper bound: asking for Edit
        under a keeper role is reduced, not honoured."""
        assert "Edit" not in aa.narrow_tools("keeper", ["Read", "Edit", "Bash"])

    def test_asking_for_nothing_yields_the_role_default(self):
        assert aa.narrow_tools("keeper", None) == aa.tools_for_role("keeper")

    def test_narrowing_an_inert_role_stays_inert(self):
        assert aa.narrow_tools("broker", ["Read", "Bash"]) == []


class TestDescription:
    def test_every_role_describes_itself(self):
        for role in ("maker", "reviewer", "triager", "keeper", "broker"):
            assert aa.describe(role).endswith(".")

    def test_broker_and_roleless_read_differently(self):
        """Both write nothing, but for different reasons, and the page should
        not tell the operator an intentional broker is a misconfiguration."""
        assert aa.describe("broker") != aa.describe(None)
        assert "Releases information only" in aa.describe("broker")
        assert "No role declared" in aa.describe(None)
