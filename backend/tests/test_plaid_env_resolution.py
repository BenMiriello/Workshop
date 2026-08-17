"""PLAID_ENV must be the knob that actually picks the Plaid host.

The bug: PLAID_ENV was advertised (secrets.env.example, app_registry
secret_env, security_audit.sh) as the thing an operator sets, but the code
only ever read PLAID_ENV_URL and defaulted to production.plaid.com. An
operator who set PLAID_ENV=sandbox, believing that was enough, would still
hit production against real bank connections.

These tests exercise services.finance_plaid._plaid_base() and
services.plaid_verify._plaid_base() directly — no network calls, no secrets.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import finance_plaid, plaid_verify  # noqa: E402


@pytest.mark.parametrize("module", [finance_plaid, plaid_verify])
class TestPlaidBaseResolution:
    def test_sandbox_resolves_to_sandbox_host(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "sandbox")
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        assert module._plaid_base() == "https://sandbox.plaid.com"

    def test_development_resolves_to_development_host(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "development")
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        assert module._plaid_base() == "https://development.plaid.com"

    def test_production_resolves_to_production_host(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "production")
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        assert module._plaid_base() == "https://production.plaid.com"

    def test_env_value_is_case_insensitive(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "Sandbox")
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        assert module._plaid_base() == "https://sandbox.plaid.com"

    def test_explicit_url_override_wins_even_over_a_valid_env(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "sandbox")
        monkeypatch.setenv("PLAID_ENV_URL", "https://plaid.example.internal")
        assert module._plaid_base() == "https://plaid.example.internal"

    def test_unset_env_refuses_rather_than_defaulting_to_production(self, module, monkeypatch):
        monkeypatch.delenv("PLAID_ENV", raising=False)
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        with pytest.raises(RuntimeError, match="PLAID_ENV"):
            module._plaid_base()

    def test_unrecognized_env_refuses_rather_than_defaulting_to_production(self, module, monkeypatch):
        monkeypatch.setenv("PLAID_ENV", "not-a-real-environment")
        monkeypatch.delenv("PLAID_ENV_URL", raising=False)
        with pytest.raises(RuntimeError, match="PLAID_ENV"):
            module._plaid_base()


def test_fetch_key_fails_closed_when_env_unset(monkeypatch):
    """On the webhook-verify path, an unset/unknown PLAID_ENV must drop the
    webhook (return None) rather than raise a 500 or silently hit production."""
    monkeypatch.setattr(plaid_verify, "PLAID_CLIENT_ID", "fake-id")
    monkeypatch.setattr(plaid_verify, "PLAID_SECRET", "fake-secret")
    monkeypatch.delenv("PLAID_ENV", raising=False)
    monkeypatch.delenv("PLAID_ENV_URL", raising=False)
    assert plaid_verify._fetch_key("some-kid") is None
