"""Push token registry + disabled-send seam (services/push.py).

Tokens are partitioned per owner now, so each test pins a data owner and an
isolated runtime dir."""
import asyncio

import pytest

from services import push, ownership


@pytest.fixture(autouse=True)
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    yield
    ownership.clear_current_owner()


def test_register_dedup_and_remove():
    push.register_token("abc", "ios")
    push.register_token("abc", "ios")  # same token -> updated, not duplicated
    push.register_token("def", "ios")
    assert len(push.list_tokens()) == 2
    assert push.remove_token("abc")["removed"] == 1
    assert len(push.list_tokens()) == 1


def test_register_rejects_empty():
    with pytest.raises(ValueError):
        push.register_token("")


def test_status_and_send_disabled_when_unconfigured(monkeypatch):
    monkeypatch.setattr(push, "APNS_KEY_PATH", "")
    assert push.is_enabled() is False
    assert push.status()["configured"] is False

    push.register_token("abc")
    result = asyncio.run(push.send_push("Title", "Body"))
    assert result["sent"] == 0
    assert result["disabled"] is True


def test_tokens_are_partitioned_per_owner():
    push.register_token("owner1-token")
    ownership.set_current_owner(2)
    assert push.list_tokens() == []  # owner 2 sees none of owner 1's tokens
    push.register_token("owner2-token")
    assert len(push.list_tokens()) == 1
    ownership.set_current_owner(1)
    assert [t["token"] for t in push.list_tokens()] == ["owner1-token"]
