"""The agent self-knowledge preamble (agent-interaction-model 2026-07-31, 7.4).

The value of this block is entirely in it being DERIVED. A hand-written "you are
a careful reviewer with file access" in an agent definition keeps asserting file
access after the tools are taken away; a derived one cannot. So what these tests
pin is not the wording — it is that every claim tracks the record and the run
config, and that a bound is stated only when it actually holds.
"""
from services import chat_context as cc
from wsplatform import paths


def _preamble(**over):
    kw = dict(
        name="Reviewer", slug="reviewer", role="reviewer", scope="workshop",
        cwd=str(paths.workshop_root()), tools=None,
        triggers=None, mentionable=False, delegatable=False,
    )
    kw.update(over)
    return cc.agent_self_preamble(**kw)


def test_states_identity_scope_and_workspace():
    out = _preamble(name="Widgets builder", slug="widgets-builder", role="maker",
                    scope="project:widgets", cwd="/tmp/widgets")
    assert "Widgets builder (@widgets-builder)" in out
    assert "a maker agent" in out
    assert "project:widgets" in out
    assert "WORKSPACE: /workspace" in out
    assert "/tmp/widgets" not in out


def test_tools_line_tracks_the_run_config():
    """The one claim most likely to be false if it were written by hand."""
    assert "TOOLS: none this turn" in _preamble(tools=None)
    assert "Claim you will do something" in _preamble(tools=[])

    on = _preamble(tools=["Read", "Grep", "Glob"])
    # With tools, the "you cannot act" bound is FALSE and must not be asserted.
    assert "Claim you will do something" not in on


def test_the_tools_line_names_the_grant_rather_than_describing_it():
    """It said "the Claude Code toolset" whatever it had actually been given.

    A triager holds Read, Grep and Glob. Told it has "the Claude Code toolset",
    it will agree to edit a file and then find it has no Edit. The line is built
    from the same list handed to the CLI, so it cannot promise more."""
    out = _preamble(tools=["Read", "Grep", "Glob"])
    assert "TOOLS: Read, Grep, Glob" in out
    assert "toolset" not in out
    for absent in ("Write", "Edit", "Bash"):
        assert absent not in out


def test_capabilities_are_never_asserted_to_the_agent():
    """`capabilities` is free text an agent writes about itself, and it was
    rendered here under a header calling this block the enforced truth while the
    caller passed mcp_servers=[]. A bridge agent declares discord/plaid/google-docs; only
    plaid is even an installed MCP server. The agent must not be told it has
    them."""
    import inspect
    assert "capabilities" not in inspect.signature(cc.agent_self_preamble).parameters
    out = _preamble(tools=["Read"])
    assert "CAPABILITIES" not in out
    assert "discord" not in out


def test_delegate_bound_only_when_it_holds():
    assert "You are not a delegate" in _preamble(delegatable=False)
    assert "not a delegate" not in _preamble(delegatable=True)


def test_reach_is_derived_from_the_record():
    none_ = _preamble()
    assert "REACHED BY" not in none_

    out = _preamble(mentionable=True, delegatable=True,
                    triggers=[{"kind": "cron", "cron_expression": "0 */6 * * *"}])
    assert "@reviewer" in out
    assert "being handed a ticket" in out
    assert "cron trigger" in out


def test_optional_lines_are_omitted_not_empty():
    out = _preamble(triggers=[])
    assert "REACHED BY:" not in out


def test_scope_names_the_boundary_it_is_bounding():
    out = _preamble(scope="project:widgets")
    assert "Take on work outside project:widgets" in out
