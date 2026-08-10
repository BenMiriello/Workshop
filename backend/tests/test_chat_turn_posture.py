"""Where a turn's effort and permission mode come from, and where plan mode goes.

Three things are pinned here, all of them about PRECEDENCE rather than about any
single value:

  1. the resolution chain is turn -> thread -> operator pref -> model/platform
     default, and a tier that cannot supply a usable value is skipped rather
     than blanking the ones below it;
  2. an @mentioned agent's own declared posture wins over the chat's, checked
     against the model the agent will actually run on;
  3. approving a plan returns the chat to the mode it was in BEFORE plan, not to
     one fixed landing mode.

The operator pref is read through `chat_store`, so every test that exercises it
pins an owner — an unpinned read is deliberately silent (no preference) and would
make these pass for the wrong reason.
"""

import pytest

from routes.chat import (
    POST_PLAN_MODE,
    _agent_effort,
    _mode_suggestion,
    _post_plan_mode,
    _resolve_effort,
    _resolve_permission_mode,
)
from services import (
    agents_registry,
    build_streaming,
    chat_context,
    chat_store,
    model_caps,
    ownership,
)


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


# ---------------------------------------------------------------- effort chain


class TestEffortPrecedence:
    def test_the_pref_is_used_when_turn_and_thread_are_silent(self, owned):
        chat_store.set_default_effort("max")
        assert _resolve_effort(None, {}, "claude-opus-5") == "max"

    def test_the_thread_outranks_the_pref(self, owned):
        chat_store.set_default_effort("max")
        assert _resolve_effort(None, {"effort": "low"}, "claude-opus-5") == "low"

    def test_the_turn_outranks_both(self, owned):
        chat_store.set_default_effort("max")
        assert _resolve_effort("medium", {"effort": "low"}, "claude-opus-5") == "medium"

    def test_a_stale_thread_level_falls_through_to_the_pref(self, owned):
        # The thread kept `xhigh` from when it was on Opus; Sonnet 4.6 has no
        # xhigh rung. That must not blank out a preference the model DOES have.
        chat_store.set_default_effort("max")
        assert _resolve_effort(None, {"effort": "xhigh"}, "claude-sonnet-4-6") == "max"

    def test_a_pref_this_model_lacks_lands_on_the_model_default(self, owned):
        chat_store.set_default_effort("xhigh")
        assert _resolve_effort(None, {}, "claude-sonnet-4-6") is None

    def test_an_explicit_unsupported_level_still_beats_the_pref(self, owned):
        # The operator's live click wins over every stored tier, even when the
        # win is "fall back to the model default".
        chat_store.set_default_effort("max")
        assert _resolve_effort("xhigh", {}, "claude-sonnet-4-6") is None

    def test_no_pref_sends_no_flag(self, owned):
        assert _resolve_effort(None, {}, "claude-opus-5") is None

    def test_an_unpinned_owner_reads_as_no_preference(self):
        # No owner: prefs are a soft layer, so this resolves rather than raising
        # out of the middle of a turn.
        assert _resolve_effort(None, {}, "claude-opus-5") is None


class TestEffortPrefStorage:
    def test_it_round_trips(self, owned):
        assert chat_store.set_default_effort("low") == "low"
        assert chat_store.get_default_effort() == "low"

    def test_empty_clears_it(self, owned):
        chat_store.set_default_effort("low")
        assert chat_store.set_default_effort("") is None
        assert chat_store.get_default_effort() is None

    def test_a_non_level_is_refused(self, owned):
        with pytest.raises(ValueError):
            chat_store.set_default_effort("ultra")

    def test_a_hand_edited_junk_value_reads_as_no_preference(self, owned):
        chat_store._write_pref("default_effort", "ultra")
        assert chat_store.get_default_effort() is None


# ------------------------------------------------------- permission mode chain


class TestPermissionModePrecedence:
    def test_the_pref_is_used_when_turn_and_thread_are_silent(self, owned):
        chat_store.set_default_permission_mode("default")
        assert _resolve_permission_mode(None, {}) == "default"

    def test_the_thread_outranks_the_pref(self, owned):
        chat_store.set_default_permission_mode("default")
        assert _resolve_permission_mode(None, {"permission_mode": "acceptEdits"}) == "acceptEdits"

    def test_the_turn_outranks_both(self, owned):
        chat_store.set_default_permission_mode("default")
        assert _resolve_permission_mode("plan", {"permission_mode": "acceptEdits"}) == "plan"

    def test_no_pref_lands_on_the_platform_default(self, owned):
        assert _resolve_permission_mode(None, {}) == model_caps.DEFAULT_PERMISSION_MODE

    def test_an_unpinned_owner_reads_as_no_preference(self):
        assert _resolve_permission_mode(None, {}) == model_caps.DEFAULT_PERMISSION_MODE


class TestPermissionModePrefStorage:
    def test_it_round_trips(self, owned):
        assert chat_store.set_default_permission_mode("plan") == "plan"
        assert chat_store.get_default_permission_mode() == "plan"

    def test_manual_is_taken_as_the_alias_the_cli_accepts(self, owned):
        assert chat_store.set_default_permission_mode("manual") == "default"

    def test_empty_clears_it(self, owned):
        chat_store.set_default_permission_mode("plan")
        assert chat_store.set_default_permission_mode("") is None
        assert chat_store.get_default_permission_mode() is None

    def test_bypass_cannot_be_made_the_default(self, owned):
        # It is a real mode (the unattended paths pass it), but it is not one a
        # chat may pick, so it is not one the operator may default a chat to.
        with pytest.raises(ValueError):
            chat_store.set_default_permission_mode("bypassPermissions")

    def test_a_hand_edited_bypass_reads_as_no_preference(self, owned):
        chat_store._write_pref("default_permission_mode", "bypassPermissions")
        assert chat_store.get_default_permission_mode() is None
        assert _resolve_permission_mode(None, {}) == model_caps.DEFAULT_PERMISSION_MODE


class TestThePayloadShowsWhatWillRun:
    """The composer displays the resolved default, so the payload has to carry
    the operator's preference rather than the bare platform value."""

    def test_the_mode_payload_follows_the_pref(self):
        assert model_caps.modes_payload("plan")["default"] == "plan"

    def test_a_mode_the_picker_does_not_offer_is_ignored(self):
        assert model_caps.modes_payload("bypassPermissions")["default"] == \
            model_caps.DEFAULT_PERMISSION_MODE

    def test_the_effort_payload_follows_the_pref(self):
        assert model_caps.caps_for("claude-opus-5", "max")["efforts"]["default"] == "max"

    def test_a_level_this_model_lacks_falls_back_to_the_model_default(self):
        caps = model_caps.caps_for("claude-sonnet-4-6", "xhigh")
        assert caps["efforts"]["default"] == "high"

    def test_the_providers_entry_the_composer_fetches_carries_it(self, owned):
        # GET /api/chat/providers is the one fetch the composer makes for both
        # pickers, so the pref has to reach it there or the control shows one
        # value and the turn runs another.
        chat_store.set_default_effort("max")
        chat_store.set_default_permission_mode("plan")
        entry = chat_context._provider_entry(chat_context.get_provider("general"))
        assert entry["modes"]["default"] == "plan"
        assert entry["model_caps"]["claude-opus-5"]["efforts"]["default"] == "max"


# ------------------------------------------------------------ plan-mode revert


class TestPlanModeReturnsWhereItCameFrom:
    def test_entering_plan_records_the_mode_in_force(self, owned):
        tid = chat_store.create_thread("general")["id"]
        chat_store.set_thread_permission_mode(tid, "default")
        t = chat_store.set_thread_permission_mode(tid, "plan")
        assert t["permission_mode"] == "plan"
        assert t["pre_plan_permission_mode"] == "default"
        assert _post_plan_mode(t) == "default"

    def test_a_chat_on_no_mode_records_what_it_was_actually_running_at(self, owned):
        # "Following the default" and "recorded nothing" must not be the same
        # absent field, or a chat that never touched the control would land on
        # the fallback instead of on its own posture.
        tid = chat_store.create_thread("general")["id"]
        t = chat_store.set_thread_permission_mode(tid, "plan")
        assert t["pre_plan_permission_mode"] == model_caps.DEFAULT_PERMISSION_MODE

    def test_it_records_the_operator_pref_when_that_is_what_is_in_force(self, owned):
        chat_store.set_default_permission_mode("default")
        tid = chat_store.create_thread("general")["id"]
        t = chat_store.set_thread_permission_mode(tid, "plan")
        assert t["pre_plan_permission_mode"] == "default"

    def test_leaving_plan_spends_the_note(self, owned):
        tid = chat_store.create_thread("general")["id"]
        chat_store.set_thread_permission_mode(tid, "acceptEdits")
        chat_store.set_thread_permission_mode(tid, "plan")
        t = chat_store.set_thread_permission_mode(tid, "acceptEdits")
        assert "pre_plan_permission_mode" not in t

    def test_re_entering_plan_does_not_overwrite_the_note_with_plan(self, owned):
        tid = chat_store.create_thread("general")["id"]
        chat_store.set_thread_permission_mode(tid, "default")
        chat_store.set_thread_permission_mode(tid, "plan")
        t = chat_store.set_thread_permission_mode(tid, "plan")
        assert t["pre_plan_permission_mode"] == "default"

    def test_nothing_recorded_falls_back_to_the_constant(self):
        assert _post_plan_mode({}) == POST_PLAN_MODE
        assert _post_plan_mode({"permission_mode": "plan"}) == POST_PLAN_MODE

    def test_a_recorded_plan_is_refused_so_approval_cannot_trap_the_chat(self):
        assert _post_plan_mode({"pre_plan_permission_mode": "plan"}) == POST_PLAN_MODE

    def test_junk_falls_back_rather_than_raising(self):
        assert _post_plan_mode({"pre_plan_permission_mode": "sideways"}) == POST_PLAN_MODE


def _turn_with(segment: dict):
    """A live turn carrying one approval segment, registered so the route-level
    helpers can find it by id. Returns the turn id."""
    turn = build_streaming.Turn(id="turn-posture", thread_id="th", agent_slug="a")
    turn.segments.append(segment)
    build_streaming.TURNS[turn.id] = turn
    return turn.id


@pytest.fixture()
def clean_turns():
    try:
        yield
    finally:
        build_streaming.TURNS.pop("turn-posture", None)


class TestApprovingAPlanUsesTheRecordedMode:
    """`_mode_suggestion` is what actually moves the chat when the operator
    clicks Approve on a plan."""

    def test_the_recorded_mode_beats_the_clis_generic_suggestion(self, clean_turns):
        # The CLI proposes a landing spot without knowing the operator was on
        # Manual before; the thread recorded where the chat actually came from.
        tid = _turn_with({
            "t": "approval", "id": "r1", "tool": "ExitPlanMode",
            "suggestions": [{"type": "setMode", "mode": "acceptEdits"}],
        })
        thread = {"permission_mode": "plan", "pre_plan_permission_mode": "default"}
        assert _mode_suggestion(tid, "r1", thread) == "default"

    def test_nothing_recorded_lands_on_the_constant(self, clean_turns):
        tid = _turn_with({"t": "approval", "id": "r1", "tool": "ExitPlanMode"})
        assert _mode_suggestion(tid, "r1", {"permission_mode": "plan"}) == POST_PLAN_MODE

    def test_an_ordinary_approval_still_follows_the_cli(self, clean_turns):
        # "Allow and stop asking" IS the vendor's setMode suggestion; the
        # pre-plan note has nothing to do with it.
        tid = _turn_with({
            "t": "approval", "id": "r1", "tool": "Write",
            "suggestions": [{"type": "setMode", "mode": "acceptEdits"}],
        })
        thread = {"permission_mode": "default", "pre_plan_permission_mode": "auto"}
        assert _mode_suggestion(tid, "r1", thread) == "acceptEdits"

    def test_an_ordinary_approval_with_no_suggestion_moves_nothing(self, clean_turns):
        tid = _turn_with({"t": "approval", "id": "r1", "tool": "Write"})
        assert _mode_suggestion(tid, "r1", {}) is None

    def test_an_unknown_turn_moves_nothing(self):
        assert _mode_suggestion("no-such-turn", "r1", {}) is None


# ------------------------------------------------------ per-agent declarations


def _write_agent(dir_path, slug: str, frontmatter: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{slug}.md").write_text(f"---\n{frontmatter}\n---\n\nBody.\n")


class TestAgentFrontmatter:
    def test_both_fields_are_parsed(self, tmp_path):
        _write_agent(tmp_path, "careful",
                     "name: Careful\nrole: reviewer\neffort: max\npermission_mode: default")
        rec = agents_registry._record_from_def(tmp_path / "careful.md", "workshop")
        assert rec["effort"] == "max"
        assert rec["permission_mode"] == "default"

    def test_declaring_nothing_declares_nothing(self, tmp_path):
        _write_agent(tmp_path, "plain", "name: Plain\nrole: maker")
        rec = agents_registry._record_from_def(tmp_path / "plain.md", "workshop")
        assert rec["effort"] is None
        assert rec["permission_mode"] is None

    def test_a_non_level_is_ignored_not_raised(self, tmp_path):
        # An operator addressing an agent should get an answer, not a stack
        # trace about a definition file they may not have written.
        _write_agent(tmp_path, "typo", "name: Typo\neffort: ultra")
        assert agents_registry._record_from_def(tmp_path / "typo.md", "workshop")["effort"] is None

    def test_manual_is_taken_as_the_cli_alias(self, tmp_path):
        _write_agent(tmp_path, "aliased", "name: Aliased\npermission_mode: manual")
        rec = agents_registry._record_from_def(tmp_path / "aliased.md", "workshop")
        assert rec["permission_mode"] == "default"

    def test_a_definition_cannot_declare_bypass(self, tmp_path):
        # A mention runs inside a chat, and bypass is not a posture this system
        # offers a chat. The point of the field is being MORE careful.
        _write_agent(tmp_path, "reckless", "name: Reckless\npermission_mode: bypassPermissions")
        rec = agents_registry._record_from_def(tmp_path / "reckless.md", "workshop")
        assert rec["permission_mode"] is None


class TestAgentPostureWinsOverTheChat:
    def test_the_agents_level_beats_the_chats(self):
        assert _agent_effort({"effort": "low"}, "a", "claude-opus-5", "max") == "low"

    def test_the_chats_level_stands_when_the_agent_declares_nothing(self):
        assert _agent_effort({}, "a", "claude-opus-5", "max") == "max"

    def test_a_level_the_agents_own_model_lacks_is_skipped(self):
        # The agent pins Sonnet 4.6 inside a chat on Opus 5: `--effort xhigh` is
        # accepted by the CLI and then ignored by the API, which is exactly the
        # control-that-does-nothing model_caps exists to prevent.
        assert _agent_effort({"effort": "xhigh"}, "a", "claude-sonnet-4-6", "low") == "low"

    def test_the_chats_level_is_rechecked_against_the_agents_model_too(self):
        assert _agent_effort({}, "a", "claude-sonnet-4-6", "xhigh") is None

    def test_nothing_anywhere_sends_no_flag(self):
        assert _agent_effort({}, "a", "claude-opus-5", None) is None
