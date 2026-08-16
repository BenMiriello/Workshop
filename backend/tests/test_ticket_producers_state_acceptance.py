"""The three ticket producers ask for acceptance and boundaries, in one vocabulary.

Measured 2026-08-13: 74% of open tickets failed the acceptance check and 68%
failed boundaries. All three producers are prompts, and the fix is a prompt
change in each — but only if they keep composing through `ticket_format`, which
is the module that exists precisely because these labels were once written out
independently in three places and did not agree.

So these tests assert two different things:

  * the prompts REQUIRE the two fields that get dropped, and
  * what a producer emits actually passes `ticket_readiness`, which is the only
    check that matters. A prompt that asks nicely and composes with the wrong
    label is the exact bug `ticket_format` was created to end.
"""
import json

from routes import chat as chat_routes
from services import chat_context, ticket_drafts, ticket_format, ticket_readiness


def _readiness(text: str) -> dict:
    """`check` over a producer's `title\\nbody` output, the way the store splits it."""
    title, _, body = text.partition("\n")
    return ticket_readiness.check({"title": title.strip(), "body": body.strip()})


class TestTheDraftProposalPrompt:
    def test_it_names_every_field_the_format_module_orders(self):
        for key in ticket_format.ORDER:
            assert f'"{key}"' in ticket_drafts.PROMPT, key

    def test_it_says_the_two_dropped_fields_are_required(self):
        assert "`output` AND `boundaries` ARE REQUIRED" in ticket_drafts.PROMPT

    def test_it_says_what_they_are_for(self):
        """A rule with no reason attached is the one a model drops first."""
        assert "NOTHING ELSE" in ticket_drafts.PROMPT
        assert "finish line" in ticket_drafts.PROMPT

    def test_it_offers_a_stated_none_instead_of_an_empty_boundary(self):
        assert "none: this may touch anything the objective requires" in ticket_drafts.PROMPT

    def test_a_complete_reply_composes_into_a_ready_ticket(self):
        reply = json.dumps({"tickets": [{
            "title": "Point LETTA_URL at the right instance",
            "objective": "the backend talks to the running Letta",
            "output": "the health endpoint reports letta ok and a test covers it",
            "entry_points": "backend/services/letta_client.py",
            "boundaries": "do not touch the agent registry",
            "confidence": "decided",
        }]})
        draft = ticket_drafts._parse(reply)[0]
        res = ticket_readiness.check({"title": draft["title"], "body": draft["body"]})
        assert res["ok"], res["missing"]

    def test_the_labels_are_the_ones_the_checker_looks_for(self):
        """Composed, not hand-written. The checker and the producer read one table."""
        reply = json.dumps({"tickets": [{
            "title": "T", "objective": "o", "output": "p",
            "entry_points": "backend/x.py", "boundaries": "none",
        }]})
        body = ticket_drafts._parse(reply)[0]["body"]
        for key in ticket_format.ORDER:
            assert ticket_format.states(body, key), key


class TestTheBuildChatRule:
    RULE = chat_context.TASK_PROPOSAL_BUILD_RULE

    def test_it_requires_output_and_boundaries(self):
        assert "output is REQUIRED" in self.RULE
        assert "boundaries is REQUIRED" in self.RULE

    def test_it_says_what_they_are_for(self):
        assert "NOTHING ELSE" in self.RULE

    def test_the_card_no_longer_calls_them_optional(self):
        """The field schema and the rule are read together; one saying "optional"
        undoes the other."""
        card = chat_context.TASK_PROPOSAL_CARD
        assert "the rest are optional" not in card
        assert "never empty" in card

    def test_it_still_uses_the_shared_section_labels(self):
        """The card asks for a description carrying labelled lines, and the
        checker finds a section by its LABEL, so the label is what has to be
        shared. Asserting the key name stopped meaning anything the moment the
        sections stopped being separate fields."""
        for key in ticket_format.ORDER:
            label = ticket_format.FIELDS[key][0]
            assert label in self.RULE or label in chat_context.TASK_PROPOSAL_CARD, key

    def test_the_card_asks_for_a_description_the_checker_can_read(self):
        """What the card tells the model to write must satisfy the checker that
        reads the filed ticket. Composing the card's own instruction lines into
        a body and running the real checker over it is the only way to know."""
        card = chat_context.TASK_PROPOSAL_CARD
        assert "description: type `textarea`" in card
        body = "\n".join(
            f"{ticket_format.FIELDS[key][0]}: something specific here."
            for key in ticket_format.ORDER
        )
        for key in ticket_format.ORDER:
            assert ticket_format.states(body, key), key

    def test_the_card_asks_for_no_field_the_ticket_page_owns(self):
        """A card that edits priority/effort is a second editor of fields the
        ticket already owns, which is what the tickets-in-chat plan rules out."""
        card = chat_context.TASK_PROPOSAL_CARD
        assert "priority: type" not in card
        assert "effort: type" not in card


class TestTheDistiller:
    def test_the_prompt_asks_for_the_same_four_fields(self):
        for key in ticket_format.ORDER:
            assert f'"{key}"' in chat_routes._DISTILL_PROMPT, key

    def test_the_prompt_requires_the_two_that_get_dropped(self):
        p = chat_routes._DISTILL_PROMPT
        assert "objective, output and boundaries are REQUIRED" in p
        assert "NOTHING ELSE" in p

    def test_a_json_reply_becomes_a_ready_ticket(self):
        out = chat_routes._distilled_text(json.dumps({
            "title": "Reduce empty turns",
            "objective": "a prompt that returns no assistant text is rare",
            "output": "the empty-turn rate is under 5% over 100 turns",
            "entry_points": "backend/services/build_streaming.py",
            "boundaries": "do not change the chat store schema",
        }))
        assert out.startswith("Reduce empty turns\n")
        assert _readiness(out)["ok"], _readiness(out)["missing"]

    def test_the_body_uses_the_canonical_labels(self):
        out = chat_routes._distilled_text(json.dumps({
            "title": "T", "objective": "o", "output": "p",
            "entry_points": "e", "boundaries": "b"}))
        assert out == "T\nObjective: o\nOutput: p\nStart from: e\nBoundaries: b"

    def test_a_fenced_reply_is_still_parsed(self):
        out = chat_routes._distilled_text(
            'here you go:\n```json\n{"title":"T","objective":"o","output":"p",'
            '"entry_points":"e","boundaries":"b"}\n```')
        assert out.startswith("T\nObjective: o")

    def test_a_prose_reply_is_kept_rather_than_lost(self):
        """A worse-specified ticket beats a 422 at the moment the operator
        pressed the button."""
        assert chat_routes._distilled_text("just do the thing") == "just do the thing"

    def test_json_with_no_title_falls_back_to_the_raw_text(self):
        """A body with no first line to split off is not a ticket."""
        raw = '{"objective":"o","output":"p","entry_points":"e","boundaries":"b"}'
        assert chat_routes._distilled_text(raw) == raw

    def test_nothing_in_means_nothing_out(self):
        assert chat_routes._distilled_text("") == ""
        assert chat_routes._distilled_text("   ") == ""

    def test_em_dashes_never_survive(self):
        """The operator's hard rule, and Haiku emits them regardless of the ask."""
        out = chat_routes._distilled_text(json.dumps({
            "title": "T", "objective": "a—b", "output": "p",
            "entry_points": "e", "boundaries": "b"}))
        assert "—" not in out
