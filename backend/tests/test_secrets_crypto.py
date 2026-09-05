"""Encryption at rest for the secrets store, and the fact it is dormant.

Two properties matter equally. It must actually protect the file once a key is
configured, and it must change NOTHING on a single-operator box that has no key
set, because that is the running system.
"""
from __future__ import annotations

import base64
import json
import os

import pytest

from services import secrets_crypto as sc
from services import secrets_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_SECRETS_ROOT", str(tmp_path))
    monkeypatch.delenv(sc.KEK_ENV, raising=False)
    return tmp_path


@pytest.fixture
def key(monkeypatch):
    k = sc.generate_kek()
    monkeypatch.setenv(sc.KEK_ENV, k)
    return k


# ---- dormant by default -----------------------------------------------------


def test_no_key_means_plaintext_exactly_as_before(store):
    secrets_store.set_values("proj", {"A": "1"})
    raw = (store / "proj.json").read_text()
    assert json.loads(raw) == {"A": "1"}
    assert secrets_store.load("proj") == {"A": "1"}


def test_enabled_is_false_without_a_key(store):
    assert sc.enabled() is False


def test_blank_key_is_treated_as_unset(store, monkeypatch):
    monkeypatch.setenv(sc.KEK_ENV, "   ")
    assert sc.enabled() is False


# ---- with a key -------------------------------------------------------------


def test_a_stored_value_is_not_on_disk_in_the_clear(store, key):
    secrets_store.set_values("proj", {"API": "sk-ant-supersecret"})
    raw = (store / "proj.json").read_text()
    assert "sk-ant-supersecret" not in raw
    assert "supersecret" not in raw


def test_round_trip(store, key):
    secrets_store.set_values("proj", {"API": "sk-ant-x", "OTHER": "y"})
    assert secrets_store.load("proj") == {"API": "sk-ant-x", "OTHER": "y"}


def test_the_file_is_still_0600(store, key):
    secrets_store.set_values("proj", {"API": "sk-ant-x"})
    assert oct((store / "proj.json").stat().st_mode & 0o777) == "0o600"


def test_merge_and_delete_still_work_encrypted(store, key):
    secrets_store.set_values("proj", {"A": "1", "B": "2"})
    secrets_store.set_values("proj", {"B": ""})
    assert secrets_store.load("proj") == {"A": "1"}


def test_each_write_uses_a_fresh_data_key(store, key):
    """Identical plaintext must not produce identical ciphertext."""
    secrets_store.set_values("a", {"K": "same"})
    secrets_store.set_values("b", {"K": "same"})
    first = json.loads((store / "a.json").read_text())
    second = json.loads((store / "b.json").read_text())
    assert first["body"] != second["body"]
    assert first["dek"] != second["dek"]


# ---- migration --------------------------------------------------------------


def test_a_plaintext_file_is_still_readable_after_a_key_is_added(store, monkeypatch):
    secrets_store.set_values("proj", {"A": "1"})
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    assert secrets_store.load("proj") == {"A": "1"}


def test_the_next_write_encrypts_it(store, monkeypatch):
    secrets_store.set_values("proj", {"A": "1"})
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    secrets_store.set_values("proj", {"B": "2"})
    raw = (store / "proj.json").read_text()
    assert sc.is_encrypted(raw)
    assert secrets_store.load("proj") == {"A": "1", "B": "2"}


# ---- failing loudly ---------------------------------------------------------


def test_an_encrypted_file_with_no_key_raises_rather_than_reading_empty(store, monkeypatch):
    """Reporting "no credential configured" because the KEY is missing would
    send the operator off to paste their credentials again."""
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    secrets_store.set_values("proj", {"A": "1"})
    monkeypatch.delenv(sc.KEK_ENV)
    with pytest.raises(sc.SecretsKeyError) as e:
        secrets_store.load("proj")
    assert sc.KEK_ENV in str(e.value)


def test_the_wrong_key_raises(store, monkeypatch):
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    secrets_store.set_values("proj", {"A": "1"})
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    with pytest.raises(sc.SecretsKeyError):
        secrets_store.load("proj")


def test_tampering_is_detected(store, key):
    secrets_store.set_values("proj", {"A": "1"})
    p = store / "proj.json"
    env = json.loads(p.read_text())
    body = bytearray(base64.b64decode(env["body"]))
    body[0] ^= 0xFF
    env["body"] = base64.b64encode(bytes(body)).decode()
    p.write_text(json.dumps(env))
    with pytest.raises(sc.SecretsKeyError):
        secrets_store.load("proj")


def test_a_file_moved_between_principals_fails_authentication(store, key):
    """The slug is bound in as additional authenticated data, so renaming one
    principal's file over another's does not hand over their credentials."""
    secrets_store.set_values("principal:2", {"API": "twos-key"})
    stolen = (store / "principal:2.json").read_text()
    (store / "principal:1.json").write_text(stolen)
    with pytest.raises(sc.SecretsKeyError) as e:
        secrets_store.load("principal:1")
    assert "different principal" in str(e.value)


@pytest.mark.parametrize("bad", ["short", "", "   ", "not-base64!!", "aGVsbG8="])
def test_a_malformed_key_is_refused(monkeypatch, bad):
    monkeypatch.setenv(sc.KEK_ENV, bad)
    if not bad.strip():
        assert sc.kek() is None
        return
    with pytest.raises(sc.SecretsKeyError):
        sc.kek()


def test_a_passphrase_is_refused_rather_than_hashed(monkeypatch):
    """Hashing a passphrase would look like it works while silently giving a key
    with whatever entropy the passphrase had."""
    monkeypatch.setenv(sc.KEK_ENV, "correct horse battery staple")
    with pytest.raises(sc.SecretsKeyError):
        sc.kek()


def test_hex_keys_are_accepted(monkeypatch):
    monkeypatch.setenv(sc.KEK_ENV, os.urandom(32).hex())
    assert len(sc.kek()) == 32


def test_generate_kek_is_usable(monkeypatch):
    monkeypatch.setenv(sc.KEK_ENV, sc.generate_kek())
    assert len(sc.kek()) == 32


def test_is_encrypted_never_raises():
    for blob in ("", "{", "null", "[]", "plain text", json.dumps({"a": 1})):
        assert sc.is_encrypted(blob) in (True, False)


# ---- the credential seam on top of it ---------------------------------------


def test_per_principal_credentials_are_encrypted_end_to_end(store, key, tmp_path,
                                                            monkeypatch):
    from services import model_credentials as mc
    from services import ownership

    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setenv(mc.MODE_ENV, mc.MODE_MULTI_USER)
    mc.set_credential(1, "anthropic_api", "sk-ant-principal-one")
    raw = mc.credentials_path(1).read_text()
    assert "sk-ant-principal-one" not in raw
    assert mc.resolve(1, "anthropic_api").value == "sk-ant-principal-one"


def test_a_store_stays_readable_after_it_moves_to_the_owner_partition(
        store, key, tmp_path, monkeypatch):
    """The move must not cost the operator their keys.

    The slug is bound into both layers of the envelope as AAD, so an encrypted
    file that moved would fail authentication if the slug moved with it. It does
    not: the file name changes and `principal:<id>` does not.
    """
    from services import model_credentials as mc
    from services import ownership

    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setenv(mc.MODE_ENV, mc.MODE_MULTI_USER)

    # Write it the old way: encrypted, in the flat secrets directory.
    secrets_store.set_values("principal:3", {"anthropic_api.credential": "sk-ant-old"})
    legacy = store / "principal:3.json"
    assert legacy.exists() and sc.is_encrypted(legacy.read_text())

    assert mc.migrate_legacy_files() == 1
    assert not legacy.exists()
    assert mc.credentials_path(3).exists()
    assert mc.resolve(3, "anthropic_api").value == "sk-ant-old"
    # Idempotent: a second pass finds nothing left to move.
    assert mc.migrate_legacy_files() == 0
