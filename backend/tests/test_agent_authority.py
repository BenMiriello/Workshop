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


class TestReversibility:
    """Run freely through reversible steps; stop before what cannot be undone.

    Keyed on whether anyone is WATCHING rather than on the role, because
    "irreversible actions require approval" is only satisfiable when there is
    someone to approve. A manual invocation has the operator right there; an
    unattended run has nobody, so for it the answer is no rather than a prompt
    into the void.
    """

    def test_unattended_runs_lose_the_irreversible_commands(self):
        denied = aa.disallowed_for(unattended=True)
        assert "Bash(git push:*)" in denied
        assert "Bash(gh pr merge:*)" in denied

    def test_a_watched_run_denies_nothing(self):
        assert aa.disallowed_for(unattended=False) == []

    def test_reactive_is_watched(self):
        """Reactive means manual invocation only, so a human is present."""
        assert aa.is_unattended("reactive") is False
        assert aa.is_unattended(None) is False

    def test_supervised_and_autonomous_are_unattended(self):
        assert aa.is_unattended("supervised") is True
        assert aa.is_unattended("autonomous") is True

    def test_the_deny_list_does_not_take_away_ordinary_work(self):
        """A maker must still be able to edit and commit locally, or the gate
        has stopped the work rather than the irreversible part of it."""
        denied = set(aa.disallowed_for(unattended=True))
        for ordinary in ("Bash(git commit:*)", "Edit", "Write", "Bash(pytest:*)"):
            assert ordinary not in denied


class TestTheGateReachesTheUnattendedRun:
    """The denylist is only worth having where nobody is watching, and until now
    the only caller was the one place someone always is.

    `disallowed_for` was asked exactly once, from the chat's @mention path, where
    the operator has just typed the mention — so it returned `[]` every time it
    was ever called. The sweep it was designed for (`dispatch_triggers.sh` ->
    `project_ralph.sh` -> `ralph.sh`) invoked `claude -p` with no `--allowedTools`,
    no `--disallowedTools` and no `--permission-mode`, inheriting a user-scope
    settings file that allows `Bash(git push:*)` at `defaultMode: auto`.

    These tests pin both halves of the wiring: the shell's entry point, and the
    fact that ralph.sh actually passes the flag to the CLI.
    """

    def _ralph(self) -> str:
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "scripts" / "ralph.sh"
        return p.read_text()

    def test_the_shell_entry_point_prints_the_patterns(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            assert aa.main(["denied", "--unattended"]) == 0
        assert buf.getvalue().split() != []
        assert buf.getvalue().splitlines() == list(aa.IRREVERSIBLE_PATTERNS)

    def test_the_shell_entry_point_denies_nothing_when_watched(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            assert aa.main(["denied"]) == 0
        assert buf.getvalue() == ""

    def test_ralph_asks_this_module_rather_than_repeating_the_list(self):
        """One definition. A second copy in bash is a denylist that drifts."""
        src = self._ralph()
        assert "services.agent_authority denied --unattended" in src
        for pattern in aa.IRREVERSIBLE_PATTERNS:
            assert pattern not in src, f"ralph.sh hardcodes {pattern}"

    def test_ralph_passes_the_deny_flag_to_the_cli(self):
        """The flag has to reach the process, not just be computed near it."""
        src = self._ralph()
        assert "DENY_FLAGS=(--disallowedTools" in src
        invocation = src.split('"$CLAUDE" "${RESUME_FLAG[@]}" -p "$ITER_PROMPT"')[1][:300]
        assert '"${DENY_FLAGS[@]}"' in invocation

    def test_the_sweep_scripts_do_not_push_on_their_own_behalf(self):
        """The deny list gates the AGENT's tool calls, not this script's own
        shell. If the loop ever needs to push, it must do it in bash, and this
        test is the reminder that adding it to the prompt instead will not work.
        """
        from pathlib import Path
        scripts = Path(__file__).resolve().parents[2] / "scripts"
        for name in ("ralph.sh", "project_ralph.sh", "reviewer.sh"):
            assert "git push" not in (scripts / name).read_text()
