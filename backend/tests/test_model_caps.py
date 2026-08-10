"""Effort capabilities: which levels a model honours, and how a selection resolves.

The point of this module is that the CLI accepts every level for every model, so
nothing downstream will complain about a level the API ignores. These tests are
the only place that mismatch is caught.
"""

import pytest
from fastapi import HTTPException

from services import model_caps
from routes.chat import _resolve_effort


class TestEffortsForModel:
    def test_the_5_series_has_all_five_rungs(self):
        assert model_caps.efforts_for("claude-opus-5") == (
            "low", "medium", "high", "xhigh", "max")

    def test_sonnet_4_6_has_max_but_not_xhigh(self):
        # The ladder is not a prefix of one global list, which is why anything
        # rendering it has to size itself from the list it is handed.
        levels = model_caps.efforts_for("claude-sonnet-4-6")
        assert "max" in levels and "xhigh" not in levels
        assert len(levels) == 4

    def test_haiku_has_no_effort_control(self):
        # Absent from the vendor's supported-model list, so the control is hidden
        # rather than shown doing nothing.
        assert model_caps.efforts_for("claude-haiku-4-5-20251001") == ()

    def test_an_unknown_model_degrades_to_no_control(self):
        assert model_caps.efforts_for("gpt-9") == ()
        assert model_caps.efforts_for(None) == ()

    def test_default_is_high_where_supported_and_absent_where_not(self):
        assert model_caps.default_effort_for("claude-opus-5") == "high"
        assert model_caps.default_effort_for("claude-haiku-4-5-20251001") is None


class TestCapsPayload:
    def test_it_carries_levels_and_default(self):
        caps = model_caps.caps_for("claude-sonnet-5")
        assert caps["vendor"] == "anthropic-claude-code"
        assert caps["efforts"]["levels"][0] == "low"
        assert caps["efforts"]["default"] == "high"

    def test_modes_are_not_per_model(self):
        # A permission mode is a property of the CLI running the turn, not of the
        # model answering, so it rides on the provider entry beside the models
        # rather than being repeated inside each one.
        assert "modes" not in model_caps.caps_for("claude-opus-5")
        payload = model_caps.modes_payload()
        assert payload["default"] == "auto"
        assert payload["options"][0] == "default"


class TestNormalizeEffort:
    def test_blank_is_no_selection(self):
        assert model_caps.normalize_effort(None, "claude-opus-5") is None
        assert model_caps.normalize_effort("  ", "claude-opus-5") is None

    def test_a_supported_level_passes_through(self):
        assert model_caps.normalize_effort("max", "claude-sonnet-4-6") == "max"

    def test_a_level_this_model_lacks_is_dropped_not_rejected(self):
        # Switching model must not 400 a turn carrying a now-unsupported level.
        assert model_caps.normalize_effort("xhigh", "claude-sonnet-4-6") is None

    def test_a_non_level_raises(self):
        with pytest.raises(ValueError):
            model_caps.normalize_effort("ultra", "claude-opus-5")


class TestResolveEffort:
    def test_body_wins_over_thread(self):
        assert _resolve_effort("low", {"effort": "max"}, "claude-opus-5") == "low"

    def test_thread_is_used_when_the_body_is_silent(self):
        assert _resolve_effort(None, {"effort": "max"}, "claude-opus-5") == "max"

    def test_nothing_chosen_sends_no_flag(self):
        # None means "omit --effort", which keeps an untouched chat's argv and
        # its prompt-cache prefix exactly as they are today.
        assert _resolve_effort(None, {}, "claude-opus-5") is None

    def test_an_explicit_unsupported_level_does_not_fall_back_to_the_thread(self):
        # The operator's latest choice wins over stale stored state, even when
        # the win is "use the model default".
        assert _resolve_effort("xhigh", {"effort": "low"}, "claude-sonnet-4-6") is None

    def test_a_stored_level_the_new_model_lacks_is_ignored(self):
        assert _resolve_effort(None, {"effort": "xhigh"}, "claude-sonnet-4-6") is None

    def test_a_garbage_level_is_a_400(self):
        with pytest.raises(HTTPException) as e:
            _resolve_effort("ultra", {}, "claude-opus-5")
        assert e.value.status_code == 400


class TestBypassIsNotOffered:
    """`bypassPermissions` IS `--dangerously-skip-permissions`. The operator's
    instruction on 2026-08-09 was that this system should not offer it."""

    def test_the_picker_does_not_offer_it(self):
        assert "bypassPermissions" not in model_caps.modes_payload()["options"]

    def test_it_is_not_the_default_any_more(self):
        assert model_caps.DEFAULT_PERMISSION_MODE == "auto"

    def test_it_is_still_a_valid_value(self):
        # The unattended paths (job runners, sweeps, the voice worker) pass it
        # explicitly. They have no operator to ask, so a mode that can stop and
        # ask would auto-deny everything and report success.
        assert model_caps.normalize_permission_mode("bypassPermissions") == "bypassPermissions"

    def test_a_thread_that_stored_it_still_loads(self):
        # Rejecting it would 400 every turn on a chat that had chosen it before
        # it was withdrawn.
        assert "bypassPermissions" in model_caps.PERMISSION_MODES

    def test_the_new_default_gets_an_approval_channel(self):
        # auto can stop and ask; without the channel it silently auto-denies.
        assert model_caps.needs_approval_channel(None) is True
        assert model_caps.needs_approval_channel("auto") is True
