"""Where the three browser-reachable origins may be declared.

An origin is not a secret: it is the address a redirect, a deep link or a
GitHub App's OAuth callback is built from, and `services/installation.py`
documents ONE place for all three (`instance.env`) precisely so editing one
does not silently leave its siblings behind in a different file. This test
pins that against the repo's own templates, since the real config lives
outside the repo on the operator's machine and cannot be read from here.
"""
from __future__ import annotations

from pathlib import Path

from services import installation as inst

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECRETS_EXAMPLE = _REPO_ROOT / "infra" / "systemd" / "secrets.env.example"
_INSTANCE_EXAMPLE = _REPO_ROOT / "infra" / "systemd" / "instance.env.example"


def _declared_keys(text: str) -> set[str]:
    keys = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0])
    return keys


def test_no_origin_variable_is_declared_in_the_secrets_template():
    keys = _declared_keys(_SECRETS_EXAMPLE.read_text(encoding="utf-8"))
    leaked = keys & set(inst.ORIGIN_ENVS)
    assert not leaked, (
        f"{leaked} belong in instance.env (non-secret deployment identity), "
        "not secrets.env"
    )


def test_all_three_origin_variables_are_declared_together_in_instance_env():
    text = _INSTANCE_EXAMPLE.read_text(encoding="utf-8")
    missing = [var for var in inst.ORIGIN_ENVS if var not in text]
    assert not missing, (
        f"{missing} must be documented in instance.env.example alongside the "
        "other origin variables, so one three-value setting is not split "
        "across two files"
    )


def test_dev_origins_default_to_the_standard_vite_port(monkeypatch):
    monkeypatch.delenv("WORKSHOP_VITE_PORT", raising=False)
    assert inst.dev_origins() == (
        "http://localhost:5173", "http://127.0.0.1:5173")


def test_a_declared_vite_port_adds_its_own_origins(monkeypatch):
    monkeypatch.setenv("WORKSHOP_VITE_PORT", "6100")
    origins = inst.dev_origins()
    assert "http://localhost:6100" in origins
    assert "http://127.0.0.1:6100" in origins
    assert "http://localhost:5173" in origins


def test_an_unusable_vite_port_falls_back_to_the_standard_one(monkeypatch):
    monkeypatch.setenv("WORKSHOP_VITE_PORT", "not-a-port")
    assert inst.dev_origins() == (
        "http://localhost:5173", "http://127.0.0.1:5173")
