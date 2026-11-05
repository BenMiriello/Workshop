"""No foreground command in a turn outlives the cap.

On 2026-10-11 a chat waited in foreground polling loops of up to ten minutes
several times in one turn, after being told not to in its prompt; the
operator's messages sat unread for each one. The CLI kills a command at
BASH_MAX_TIMEOUT_MS ("Command timed out after 8s", measured against 2.1.296),
so setting it is what makes the wait impossible rather than discouraged.
"""
from services import build_streaming, chat_context


def test_every_turn_is_spawned_with_the_cap():
    env = build_streaming._turn_env({"model": "claude-opus-5-5"}, {})
    cap_ms = str(chat_context.FOREGROUND_COMMAND_CAP_S * 1000)
    assert env["BASH_MAX_TIMEOUT_MS"] == cap_ms
    assert env["BASH_DEFAULT_TIMEOUT_MS"] == cap_ms


def test_the_turn_is_told_the_same_cap():
    text = chat_context.turn_contract()
    assert f"stopped after {chat_context.FOREGROUND_COMMAND_CAP_S} seconds" in text
