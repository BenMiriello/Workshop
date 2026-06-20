"""Push token registry + disabled-send seam (services/push.py)."""
import asyncio

import pytest

from services import push


def test_register_dedup_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "TOKENS_FILE", tmp_path / "push-tokens.json")
    push.register_token("abc", "ios")
    push.register_token("abc", "ios")  # same token -> updated, not duplicated
    push.register_token("def", "ios")
    assert len(push.list_tokens()) == 2
    assert push.remove_token("abc")["removed"] == 1
    assert len(push.list_tokens()) == 1


def test_register_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "TOKENS_FILE", tmp_path / "p.json")
    with pytest.raises(ValueError):
        push.register_token("")


def test_status_and_send_disabled_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "TOKENS_FILE", tmp_path / "p.json")
    monkeypatch.setattr(push, "APNS_KEY_PATH", "")
    assert push.is_enabled() is False
    assert push.status()["configured"] is False

    push.register_token("abc")
    result = asyncio.run(push.send_push("Title", "Body"))
    assert result["sent"] == 0
    assert result["disabled"] is True
