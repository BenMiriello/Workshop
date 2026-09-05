"""One resolver, six rungs, and an answer that says which rung won.

WHAT IS ACTUALLY PINNED HERE. Not "the resolver returns a model" — it always
did, from four different places. What is pinned is that there is ONE place, that
its order is the documented one, that `source` names the rung that decided, and
that tolerance is asymmetric: an explicit ask that cannot be honoured is a 400,
and every stored rung below it is skipped with a NAMED reason so a pin the
operator cannot see never fails a turn.

The last property is the one with a failure behind it. A model id can vanish
between restarts — a local model server reloaded with a different weight — and
before this the answer depended on which caller asked: the composer's chain
fell back silently, the agent-mention path warned and fell back, and nothing
recorded which had happened.
"""

from __future__ import annotations

import dataclasses

import pytest

from services import chat_context, chat_store, llm_providers, model_resolution
from services import llm_router, model_caps, ownership

OPUS = llm_router.TIER_OPUS_5
SONNET = llm_router.TIER_SONNET
HAIKU = llm_router.TIER_HAIKU


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    """A pinned owner, so the principal-default rung is readable and writable."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _resolve(request, scope=None):
    return model_resolution.resolve_model(1, scope, request)


# ------------------------------------------------------------------ the ladder


class TestTheSixRungs:
    """Most specific first, each one demonstrated winning over the next."""

    def test_1_the_explicit_request_wins(self, owned):
        chat_store.set_default_model(SONNET)
        choice = _resolve(model_resolution.ModelRequest(
            model=OPUS, pinned_model=HAIKU, scope_model=SONNET))
        assert choice.model_id == OPUS
        assert choice.source == model_resolution.SOURCE_REQUEST

    def test_2_the_thread_pin_wins_when_nothing_was_asked(self, owned):
        choice = _resolve(model_resolution.ModelRequest(
            pinned_model=HAIKU, scope_model=SONNET))
        assert choice.model_id == HAIKU
        assert choice.source == model_resolution.SOURCE_PIN

    def test_3_the_agent_declaration_wins_over_the_scope(self, owned):
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "careful", {"model": OPUS}, chat_model=SONNET))
        assert choice.model_id == OPUS
        assert choice.source == model_resolution.SOURCE_AGENT

    def test_4_the_scope_default_wins_over_the_principal(self, owned):
        chat_store.set_default_model(HAIKU)
        choice = _resolve(model_resolution.ModelRequest(scope_model=SONNET))
        assert choice.model_id == SONNET
        assert choice.source == model_resolution.SOURCE_SCOPE

    def test_5_the_principal_default_wins_over_the_deployment(self, owned):
        chat_store.set_default_model(OPUS)
        choice = _resolve(model_resolution.ModelRequest())
        assert choice.model_id == OPUS
        assert choice.source == model_resolution.SOURCE_PRINCIPAL

    def test_6_the_deployment_default_always_answers(self, owned):
        choice = _resolve(model_resolution.ModelRequest())
        assert choice.model_id == llm_providers.deployment_default().id
        assert choice.source == model_resolution.SOURCE_DEPLOYMENT

    def test_every_source_is_a_declared_rung(self, owned):
        for request in (model_resolution.ModelRequest(model=OPUS),
                        model_resolution.ModelRequest(pinned_model=OPUS),
                        model_resolution.ModelRequest(scope_model=OPUS),
                        model_resolution.ModelRequest()):
            assert _resolve(request).source in model_resolution.SOURCES


class TestTheChoiceCarriesTheProvider:
    def test_it_names_the_provider_and_the_wire(self, owned):
        choice = _resolve(model_resolution.ModelRequest(model=OPUS))
        assert choice.provider_id == "claude_cli"
        assert choice.wire == llm_providers.WIRE_ANTHROPIC

    def test_it_is_serialisable_without_losing_the_reason(self, owned):
        choice = _resolve(model_resolution.ModelRequest(pinned_model="no-such-model"))
        payload = choice.as_dict()
        assert payload["source"] == model_resolution.SOURCE_DEPLOYMENT
        assert payload["notes"] and "no-such-model" in payload["notes"][0]


# --------------------------------------------------------------- the tolerance


class TestToleranceIsAsymmetric:
    """An explicit ask is a 400. Everything stored below it degrades, named."""

    def test_an_invalid_explicit_model_raises(self, owned):
        with pytest.raises(model_resolution.UnknownModel):
            _resolve(model_resolution.ModelRequest(model="gpt-9"))

    def test_a_vanished_pin_degrades_to_the_deployment_default(self, owned):
        choice = _resolve(model_resolution.ModelRequest(pinned_model="llama-3.1-70b"))
        assert choice.model_id == llm_providers.deployment_default().id
        assert choice.source == model_resolution.SOURCE_DEPLOYMENT

    def test_the_reason_is_NAMED_rather_than_silent(self, owned):
        choice = _resolve(model_resolution.ModelRequest(pinned_model="llama-3.1-70b"))
        assert len(choice.notes) == 1
        assert "llama-3.1-70b" in choice.notes[0]
        assert "cannot reach" in choice.notes[0]

    def test_a_vanished_pin_still_lets_a_lower_rung_answer(self, owned):
        choice = _resolve(model_resolution.ModelRequest(
            pinned_model="llama-3.1-70b", scope_model=SONNET))
        assert choice.model_id == SONNET
        assert choice.source == model_resolution.SOURCE_SCOPE

    def test_an_agent_declaring_a_model_this_build_cannot_pick_still_answers(self, owned):
        """THE TOLERANCE THAT HAD TO SURVIVE. The operator addressing an agent
        gets an answer, not a 400 about a definition file they may not have
        written."""
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "typo", {"model": "claude-opus-6"}, chat_model=SONNET))
        assert choice.model_id == SONNET
        assert "claude-opus-6" in choice.notes[0]

    def test_a_declared_tier_name_resolves(self, owned):
        """A definition says `model: sonnet` and keeps saying it when the handle
        behind that tier rotates."""
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "plain", {"model": "sonnet"}, chat_model=OPUS))
        assert choice.model_id == SONNET

    def test_a_junk_principal_default_does_not_fail_a_turn(self, owned):
        chat_store._write_pref("default_model", "not-a-model")
        choice = _resolve(model_resolution.ModelRequest())
        assert choice.model_id == llm_providers.deployment_default().id


# --------------------------------------------------- effort follows the model


class TestEffortResolvesAfterTheModel:
    def test_the_level_is_judged_against_the_model_that_won(self, owned):
        """`xhigh` exists on Opus 5 and not on Sonnet 4.6. A turn whose model
        resolved to Sonnet must drop it rather than pass a flag the CLI accepts
        and the API ignores."""
        assert model_caps.efforts_for(OPUS) and "xhigh" not in model_caps.efforts_for(SONNET)
        choice = _resolve(model_resolution.ModelRequest(
            model=SONNET, effort="xhigh"))
        assert choice.model_id == SONNET
        assert choice.effort is None

    def test_the_same_level_survives_on_a_model_that_has_the_rung(self, owned):
        choice = _resolve(model_resolution.ModelRequest(model=OPUS, effort="xhigh"))
        assert choice.effort == "xhigh"

    def test_an_agents_model_changes_which_levels_are_available(self, owned):
        """The agent pins Sonnet inside a chat on Opus; the chat's `xhigh` has
        to be re-judged against the agent's model, not the composer's."""
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "narrow", {"model": SONNET}, chat_model=OPUS, chat_effort="xhigh"))
        assert choice.model_id == SONNET
        assert choice.effort is None

    def test_a_level_that_is_not_a_level_at_all_raises(self, owned):
        with pytest.raises(ValueError):
            _resolve(model_resolution.ModelRequest(model=OPUS, effort="ultra"))


class TestPermissionModeIsAlwaysAnswered:
    def test_it_never_returns_none(self, owned):
        assert _resolve(model_resolution.ModelRequest()).permission_mode

    def test_an_agent_declaration_outranks_the_chat(self, owned):
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "careful", {"permission_mode": "plan"}, chat_model=OPUS,
            chat_permission_mode="acceptEdits"))
        assert choice.permission_mode == "plan"

    def test_nothing_anywhere_lands_on_the_platform_default(self, owned):
        assert _resolve(model_resolution.ModelRequest()).permission_mode == \
            model_caps.DEFAULT_PERMISSION_MODE


# ------------------------------------- the catalog is what is CONFIGURED


class _FakeProvider:
    """A second provider, so "the catalog is a function of configuration" can be
    tested without one existing."""

    id = "test_http"
    transport = llm_providers.TRANSPORT_HTTP
    wire = llm_providers.WIRE_ANTHROPIC
    base_url = "http://127.0.0.1:9/v1"
    auth_mode = "none"

    def models(self):
        return [llm_providers.ModelInfo(
            id="a-local-weight", label="A Local Weight", provider_id=self.id,
            wire=self.wire,
            capabilities=llm_providers.Capabilities(context_tokens=8192))]

    def reach(self):
        return llm_providers.Reach(llm_providers.REACH_READY)

    def run_turn(self, prompt, *, model, **kw):  # pragma: no cover - not called here
        raise AssertionError("no test here runs a turn")


@pytest.fixture()
def with_second_provider(monkeypatch):
    real = llm_providers.providers

    def both():
        return [*real(), _FakeProvider()]

    monkeypatch.setattr(llm_providers, "providers", both)
    return _FakeProvider()


class TestPickableIsAFunctionOfWhatIsConfigured:
    """It was a hardcoded list of six Claude ids, which is right for exactly one
    provider and wrong the moment there are two."""

    def test_a_configured_provider_adds_its_models(self, with_second_provider):
        assert "a-local-weight" in chat_context.pickable_models()

    def test_removing_it_removes_them(self):
        assert "a-local-weight" not in chat_context.pickable_models()

    def test_normalize_model_accepts_what_is_configured(self, with_second_provider):
        assert chat_context.normalize_model("a-local-weight") == "a-local-weight"

    def test_normalize_model_still_refuses_what_is_not(self):
        with pytest.raises(ValueError):
            chat_context.normalize_model("a-local-weight")

    def test_the_resolver_will_pick_it_for_a_non_agentic_turn(
            self, owned, with_second_provider):
        choice = _resolve(model_resolution.ModelRequest(
            model="a-local-weight", agentic=False))
        assert choice.provider_id == "test_http"

    def test_a_turn_that_needs_tools_will_not_run_on_a_provider_without_them(
            self, owned, with_second_provider):
        """Tools and permission modes on this platform are CLI FLAGS. A model
        reached over plain HTTP has no harness to carry them, so an agentic turn
        must not silently be handed one."""
        with pytest.raises(model_resolution.UnknownModel):
            _resolve(model_resolution.ModelRequest(
                model="a-local-weight", agentic=True))

    def test_a_pinned_model_without_a_harness_degrades_and_says_so(
            self, owned, with_second_provider):
        choice = _resolve(model_resolution.ModelRequest(
            pinned_model="a-local-weight", agentic=True))
        assert choice.provider_id == "claude_cli"
        assert "tool harness" in choice.notes[0]


class TestTheComposerPayloadShapeSurvives:
    """`describe_provider` still returns models and default_model per provider,
    which is the whole reason this was the smallest possible blast radius."""

    def test_the_entry_keys_are_unchanged(self, owned):
        entry = chat_context._provider_entry(chat_context.get_provider("general"))
        assert set(entry) == {"key", "label", "models", "default_model",
                              "tools_active", "model_caps", "modes"}

    def test_models_is_the_configured_catalog(self, owned, with_second_provider):
        entry = chat_context._provider_entry(chat_context.get_provider("general"))
        assert entry["models"] == chat_context.pickable_models()
        assert "a-local-weight" in entry["models"]

    def test_every_listed_model_has_caps(self, owned, with_second_provider):
        entry = chat_context._provider_entry(chat_context.get_provider("general"))
        assert set(entry["model_caps"]) == set(entry["models"])


class TestScopeDefaultsAreDeclaredNotInherited:
    """A scope that has merely never chosen must not look like one that chose
    the cheap tier: an inherited constant answering rung 4 makes rung 5
    unreachable."""

    def test_the_workshop_scope_declares_nothing(self):
        assert chat_context.get_provider("general").declared_model is None

    def test_a_build_scope_declares_a_strong_tier(self):
        provider = chat_context.ProjectContextProvider("self")
        assert provider.declared_model == llm_router.TIER_SONNET

    def test_default_model_still_reads_as_a_plain_attribute(self, owned):
        assert chat_context.get_provider("general").default_model == \
            llm_providers.deployment_default().id

    def test_the_operator_default_reaches_a_scope_that_declared_nothing(self, owned):
        chat_store.set_default_model(OPUS)
        choice = _resolve(model_resolution.ModelRequest(), scope="general")
        assert choice.model_id == OPUS
        assert choice.source == model_resolution.SOURCE_PRINCIPAL

    def test_a_scope_that_declared_one_is_not_moved_by_it(self, owned):
        chat_store.set_default_model(OPUS)
        request = dataclasses.replace(model_resolution.ModelRequest(),
                                      scope_model=SONNET)
        assert _resolve(request).model_id == SONNET


class TestThereIsOnlyOneResolver:
    """The ticket's own boundary: do not add a second, and do not leave the old
    chain in place for a caller that "is not ready yet"."""

    def test_the_route_no_longer_carries_its_own_chain(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "routes" / "chat.py"
        body = src.read_text()
        # The four chains this replaced. Their names may appear in prose that
        # explains the removal, so the test looks for a DEFINITION.
        for name in ("_resolve_effort", "_resolve_permission_mode", "_agent_effort"):
            assert f"def {name}(" not in body, f"{name} is back in routes/chat.py"

    def test_the_route_calls_the_resolver(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "routes" / "chat.py"
        assert "model_resolution.resolve_model(" in src.read_text()
