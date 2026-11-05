"""A chat's HTTP tool servers are told which chat and turn is calling.

`llm_router.with_caller` stamps `X-Actor`, `X-Actor-Detail`, `X-Thread` and
`X-Turn` on every HTTP server's spec, so an app that logs what was done to it
can name the chat behind each change. Stdio servers are left alone.
"""
from __future__ import annotations

from services import llm_router


def test_http_servers_carry_the_chat_and_turn():
    got = llm_router.with_caller(
        {"app": {"type": "http", "url": "http://127.0.0.1:9/mcp"},
         "local": {"type": "stdio", "command": "x"}},
        scope="app:example", thread_id="t1", turn_id="u2")
    assert got["app"]["headers"] == {"X-Actor": "agent", "X-Actor-Detail": "chat:app:example",
                                     "X-Thread": "t1", "X-Turn": "u2"}
    assert "headers" not in got["local"]


def test_headers_a_spec_already_had_are_kept():
    got = llm_router.with_caller(
        {"app": {"type": "http", "url": "u", "headers": {"Authorization": "x"}}},
        scope="s", thread_id="t", turn_id="u")
    assert got["app"]["headers"]["Authorization"] == "x"
