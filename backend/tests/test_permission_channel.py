"""The permission mode and the channel it needs, at the argv level.

The failure this guards against is silent and total: passing a mode that can
stop and ask, WITHOUT `--permission-prompt-tool stdio`, makes the CLI accept the
mode and then auto-deny every gated tool call. The turn ends "success", the file
is never written, and the only trace is a permission_denials array in the result.
Measured against CLI 2.1.143.

`--permission-prompt-tool` is not in `claude --help`. If a CLI upgrade drops it,
these tests keep passing while the feature breaks, so they are paired with the
argv assertions rather than trusted alone: see docs/chat-effort-and-mode-2026-08-09.md.
"""

import pytest

from services import build_streaming, model_caps


def meta(**kw) -> dict:
    base = {
        "turn_id": "t1", "model": "claude-opus-5", "session_id": None,
        "resume": False, "cwd": "/tmp",
    }
    base.update(kw)
    return base


def flag_value(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class TestPermissionModeArgv:
    def test_the_default_is_what_chats_have_always_run(self):
        cmd, _ = build_streaming._build_cmd(meta())
        assert flag_value(cmd, "--permission-mode") == "bypassPermissions"

    def test_bypass_gets_no_approval_channel(self):
        # Nothing can ask in bypass, so a channel would be dead weight.
        cmd, _ = build_streaming._build_cmd(meta(permission_mode="bypassPermissions"))
        assert "--permission-prompt-tool" not in cmd

    @pytest.mark.parametrize("mode", ["default", "plan", "acceptEdits", "auto"])
    def test_every_asking_mode_gets_the_channel(self, mode):
        cmd, _ = build_streaming._build_cmd(meta(permission_mode=mode))
        assert flag_value(cmd, "--permission-mode") == mode
        assert flag_value(cmd, "--permission-prompt-tool") == "stdio", (
            f"{mode} can stop and ask; without the stdio channel the CLI "
            "silently auto-denies every gated call"
        )

    def test_the_coupling_holds_on_the_narrow_tool_surface_too(self):
        # Assistant/data turns take their flags from llm_router.tool_flags, which
        # used to hardcode bypassPermissions. A mode chosen in the chat has to
        # survive that path as well.
        cmd, _ = build_streaming._build_cmd(
            meta(permission_mode="default", allowed_tools=["WebSearch"], mcp_servers=[]))
        assert flag_value(cmd, "--permission-mode") == "default"
        assert flag_value(cmd, "--permission-prompt-tool") == "stdio"

    def test_no_mode_can_ask_without_the_channel(self):
        # The invariant itself, over every mode the picker can produce.
        for mode in model_caps.PERMISSION_MODES:
            cmd, _ = build_streaming._build_cmd(meta(permission_mode=mode))
            asks = mode != "bypassPermissions"
            assert ("--permission-prompt-tool" in cmd) is asks, mode


class TestEffortArgv:
    def test_no_effort_means_no_flag(self):
        # An untouched chat spawns the argv it spawned before this feature, which
        # is also what keeps its prompt-cache prefix.
        cmd, _ = build_streaming._build_cmd(meta())
        assert "--effort" not in cmd

    def test_a_chosen_level_is_passed(self):
        cmd, _ = build_streaming._build_cmd(meta(effort="xhigh"))
        assert flag_value(cmd, "--effort") == "xhigh"


class TestPermissionModeNormalization:
    def test_manual_is_an_alias_for_default(self):
        # The CLI accepts `manual` wherever the value is typed; a value copied
        # from a terminal or a settings file has to work here too.
        assert model_caps.normalize_permission_mode("manual") == "default"

    def test_blank_defers_to_the_stored_or_platform_default(self):
        assert model_caps.normalize_permission_mode("") is None

    def test_dontask_is_not_offered(self):
        # A CI posture, not a chat posture: it auto-denies anything not
        # pre-approved, which in a chat reads exactly like the bug above.
        assert "dontAsk" not in model_caps.PERMISSION_MODES
        with pytest.raises(ValueError):
            model_caps.normalize_permission_mode("dontAsk")

    def test_an_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            model_caps.normalize_permission_mode("yolo")


class TestControlFrames:
    def test_an_allow_carries_the_input_back(self):
        # The CLI expects the (possibly edited) input echoed on an allow.
        import json
        frames = []
        orig = build_streaming._write_line
        build_streaming._write_line = lambda tid, line: frames.append(line) or True
        try:
            build_streaming.answer_permission("t1", "req-1", True, {"file_path": "/tmp/x"})
        finally:
            build_streaming._write_line = orig
        sent = json.loads(frames[0])
        assert sent["type"] == "control_response"
        assert sent["response"]["request_id"] == "req-1"
        assert sent["response"]["response"]["behavior"] == "allow"
        assert sent["response"]["response"]["updatedInput"] == {"file_path": "/tmp/x"}

    def test_a_deny_carries_a_reason_the_model_can_read(self):
        import json
        frames = []
        orig = build_streaming._write_line
        build_streaming._write_line = lambda tid, line: frames.append(line) or True
        try:
            build_streaming.answer_permission("t1", "req-2", False, message="wrong file")
        finally:
            build_streaming._write_line = orig
        sent = json.loads(frames[0])
        assert sent["response"]["response"] == {"behavior": "deny", "message": "wrong file"}


class TestApprovalSegments:
    def _turn(self):
        return build_streaming.Turn(id="t1", thread_id="th1", agent_slug="a")

    def test_a_request_becomes_a_pending_segment(self):
        turn = self._turn()
        parsed = build_streaming._parse_stream_event(
            '{"type":"control_request","request_id":"r1","request":'
            '{"subtype":"can_use_tool","tool_name":"Write","display_name":"Write",'
            '"input":{"file_path":"/tmp/x"},"description":"x",'
            '"permission_suggestions":[{"type":"setMode","mode":"acceptEdits"}]}}')
        assert parsed is not None
        kind, payload = parsed
        assert kind == "permission"
        build_streaming._segment_ingest(turn, kind, payload)
        seg = turn.segments[0]
        assert seg["t"] == "approval" and seg["state"] == "pending"
        assert seg["tool"] == "Write"
        assert seg["suggestions"][0]["mode"] == "acceptEdits"

    def test_a_journal_re_read_does_not_ask_twice(self):
        # The drive loop re-reads from offset 0 after a restart.
        turn = self._turn()
        payload = {"id": "r1", "tool": "Write", "label": "Write", "input": {}, "suggestions": []}
        build_streaming._segment_ingest(turn, "permission", payload)
        build_streaming._segment_ingest(turn, "permission", payload)
        assert len([s for s in turn.segments if s["t"] == "approval"]) == 1

    def test_an_answered_request_rebuilds_as_answered(self):
        turn = self._turn()
        turn.answers["r1"] = "allowed"
        build_streaming._segment_ingest(
            turn, "permission", {"id": "r1", "tool": "Write", "label": "Write"})
        assert turn.segments[0]["state"] == "allowed"

    def test_pending_approvals_go_stale_when_the_turn_ends(self):
        # The process it blocked is gone; live buttons would offer a decision
        # that silently does nothing.
        turn = self._turn()
        build_streaming._segment_ingest(
            turn, "permission", {"id": "r1", "tool": "Write", "label": "Write"})
        build_streaming._stale_pending_approvals(turn)
        assert turn.segments[0]["state"] == "stale"

    def test_staleness_does_not_rewrite_a_real_decision(self):
        turn = self._turn()
        build_streaming._segment_ingest(
            turn, "permission", {"id": "r1", "tool": "Write", "label": "Write"})
        turn.segments[0]["state"] = "denied"
        build_streaming._stale_pending_approvals(turn)
        assert turn.segments[0]["state"] == "denied"
