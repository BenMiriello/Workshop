"""The agent self-knowledge preamble (agent-interaction-model 2026-07-31, 7.4).

The value of this block is entirely in it being DERIVED. A hand-written "you are
a careful reviewer with file access" in an agent definition keeps asserting file
access after the tools are taken away; a derived one cannot. So what these tests
pin is not the wording — it is that every claim tracks the record and the run
config, and that a bound is stated only when it actually holds.
"""
from services import chat_context as cc


def _preamble(**over):
    kw = dict(
        name="Reviewer", slug="reviewer", role="reviewer", scope="workshop",
        cwd="/home/clawd-a/workshop", tools_active=False,
        capabilities=None, triggers=None, mentionable=False, delegatable=False,
    )
    kw.update(over)
    return cc.agent_self_preamble(**kw)


def test_states_identity_scope_and_workspace():
    out = _preamble(name="Notes builder", slug="notes-builder", role="maker",
                    scope="project:notes-app", cwd="/tmp/notes")
    assert "Notes builder (@notes-builder)" in out
    assert "a maker agent" in out
    assert "project:notes-app" in out
    assert "/tmp/notes" in out


def test_tools_line_tracks_the_run_config():
    """The one claim most likely to be false if it were written by hand."""
    assert "TOOLS: none this turn" in _preamble(tools_active=False)
    assert "Claim you will do something" in _preamble(tools_active=False)

    on = _preamble(tools_active=True)
    assert "TOOLS: the Claude Code toolset" in on
    # With tools, the "you cannot act" bound is FALSE and must not be asserted.
    assert "Claim you will do something" not in on


def test_delegate_bound_only_when_it_holds():
    assert "you are not a delegate" in _preamble(delegatable=False)
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
    out = _preamble(capabilities=[], triggers=[])
    assert "CAPABILITIES:" not in out
    assert "REACHED BY:" not in out
    assert "CAPABILITIES: consolidate." in _preamble(capabilities=["consolidate"])


def test_scope_names_the_boundary_it_is_bounding():
    out = _preamble(scope="project:birds")
    assert "Take on work outside project:birds" in out
