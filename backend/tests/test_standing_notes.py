"""A standing note is a note with `reach: always`, and the rules for writing one.

This is the behaviour that had to SURVIVE the fold of `.runtime/learnings` into
the note store (ticket 81198145, the-plan-2026-08-23.md section 2): the quality
gate that refuses a bare status token, write-time dedup and supersede, and a
provenance count. Everything else in the 1,467-line learnings.py was machinery
for a garbage collector that was never scheduled.
"""
import pytest

from services import knowledge, standing


@pytest.fixture
def scope(tmp_path, monkeypatch):
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setattr(knowledge, "scope_dir", lambda key, create=False: d)
    return "workshop"


# --- the quality gate -------------------------------------------------------

def test_a_bare_status_token_is_refused(scope):
    r = standing.record("Ralph rejected: ts_typecheck_fail", source="loop", scope=scope)
    assert r["resolution"] == "refused"
    assert "machine status" in r["reason"]
    assert standing.count(scope) == 0


def test_real_guidance_is_recorded(scope):
    r = standing.record(
        "Always run the typecheck before you claim a change is done.",
        source="operator", scope=scope)
    assert r["resolution"] == "add"
    assert standing.count(scope) == 1


def test_the_operator_is_exempt_from_the_length_rules_but_not_from_the_shape_one(scope):
    """He typed it, so it means what it says however short. A status token is
    still a status token.

    `build failed` is NOT the example to use here and this test asserted it was:
    `build` is in the instruction vocabulary, so the shape rule does not fire on
    it, in the ported gate and in the original alike. Verified against both
    before changing this. A real machine status has no instruction word in it.
    """
    assert standing.record("Prefer rows to cards.", source="operator", scope=scope)["resolution"] == "add"
    assert standing.record("ts_typecheck_fail", source="operator", scope=scope)["resolution"] == "refused"
    assert standing.record("deploy rejected", source="operator", scope=scope)["resolution"] == "refused"


def test_a_refusal_is_never_an_exception(scope):
    """Every path that writes one of these has real work to finish afterwards."""
    for bad in ("", "   ", "exit 1", "py_typecheck_fail"):
        assert standing.record(bad, source="loop", scope=scope)["resolution"] == "refused"


# --- what it writes ---------------------------------------------------------

def test_it_writes_a_note_that_the_note_store_can_see(scope):
    standing.record("Never commit without running the design gate first.",
                    source="operator", scope=scope)
    notes = knowledge.list_notes(scope)
    assert len(notes) == 1
    # It is an ORDINARY note: it has a title, so it can be listed, opened,
    # edited and deleted on /memory through routes that already exist. That is
    # the whole return on the fold.
    assert notes[0]["title"]
    raw = (knowledge.scope_dir(scope) / f"{notes[0]['id']}.md").read_text()
    assert knowledge.note_reach(raw) == "always"


def test_the_title_is_the_rule_and_not_the_reason(scope):
    standing.record(
        "Do not add multi-tenant guardrails. This is a single-operator system "
        "and the operator has corrected it twice.",
        source="operator", scope=scope)
    assert knowledge.list_notes(scope)[0]["title"] == "Do not add multi-tenant guardrails"


# --- dedup and supersede ----------------------------------------------------

def test_the_same_rule_twice_is_recorded_once(scope):
    text = "Always run the typecheck before you claim a change is done."
    assert standing.record(text, source="a", scope=scope)["resolution"] == "add"
    assert standing.record(text, source="b", scope=scope)["resolution"] == "noop"
    assert standing.count(scope) == 1


def test_recording_it_again_counts_it_again(scope):
    """The one counter worth keeping: a rule arriving twice from two places is
    the signal that it is real."""
    text = "Always run the typecheck before you claim a change is done."
    standing.record(text, source="a", scope=scope)
    standing.record(text, source="b", scope=scope)
    raw = (knowledge.scope_dir(scope) / f"{knowledge.list_notes(scope)[0]['id']}.md").read_text()
    assert standing.provenance_of(raw) == 2


def test_an_elaboration_replaces_the_rule_in_place(scope):
    """In place, so every ref and backlink that named the note still resolves.
    Superseding used to mint a new row and archive the old one, which is the
    same information and one more thing to collect."""
    short = ("Always run the typecheck before you claim a change is done, "
             "because the reviewer gate reads it and will reject the run.")
    standing.record(short, source="a", scope=scope)
    first_id = knowledge.list_notes(scope)[0]["id"]

    longer = short + " Use npm run typecheck from the repo root, never npx tsc."
    r = standing.record(longer, source="b", scope=scope)

    assert r["resolution"] in ("update", "supersede")
    assert r["note_id"] == first_id
    assert standing.count(scope) == 1
    assert "npx tsc" in knowledge.get_note(scope, first_id)


def test_two_unrelated_rules_both_survive(scope):
    standing.record("Always run the typecheck before you claim a change is done.",
                    source="a", scope=scope)
    standing.record("Never use an em-dash in any string a user can read.",
                    source="a", scope=scope)
    assert standing.count(scope) == 2


# --- the prompt block -------------------------------------------------------

def test_the_block_is_the_bodies_verbatim(scope):
    standing.record("Always run the typecheck before you claim a change is done.",
                    source="operator", scope=scope)
    b = standing.block(scope)
    assert "Always run the typecheck" in b
    assert b.startswith("- ")


def test_an_ordinary_note_is_not_in_the_block(scope):
    """`reach` is the whole distinction. A note written to be looked up must not
    turn up in every prompt."""
    knowledge._put_note_sync(scope, "ordinary", "Ordinary", "some background fact")
    assert standing.block(scope) == ""
    assert standing.count(scope) == 0


def test_the_block_holds_to_its_budget(scope):
    for i in range(40):
        standing.record(
            f"Rule number {i}: always check the {i} thing before you claim it is "
            f"done, because the gate reads it and will reject the run otherwise.",
            source="operator", scope=scope)
    b = standing.block(scope, max_tokens=100)
    assert len(b) // 4 <= 120


def test_a_rediscovered_rule_outranks_one_recorded_once(scope):
    once = "Never use an em-dash in any string a user can read."
    twice = "Always run the typecheck before you claim a change is done."
    standing.record(once, source="a", scope=scope)
    standing.record(twice, source="a", scope=scope)
    standing.record(twice, source="b", scope=scope)
    assert standing.block(scope).splitlines()[0].startswith("- Always run the typecheck")


def test_an_empty_scope_contributes_nothing(scope):
    assert standing.block(scope) == ""
