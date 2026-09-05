"""The licensing boundary, as executable rules.

The fact these tests exist to hold: a subscription credential may only ever
serve the person who owns it. Anthropic prohibits routing requests through
Free/Pro/Max credentials on behalf of other users and enforces it server side,
so a hosted deployment that did it would not merely be in breach, it would be
broken. See services/model_providers for the sourced statement.

The single most important test in this file is
`test_multi_user_refuses_the_subscription_provider`. If a future change makes it
fail, the change is wrong, not the test.
"""
from __future__ import annotations

import pytest

from services import model_credentials as mc
from services import model_providers as mp


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    # BOTH roots, and the second one matters as much as the first. A principal's
    # credentials live under the owner partition now, so a test that pinned only
    # WORKSHOP_SECRETS_ROOT wrote real files into this checkout's own
    # `.runtime/users/<id>/`. `ownership._RUNTIME` is read at import, so it is
    # patched as an attribute, which is the convention the rest of this suite
    # already uses (see test_tenancy_file_store_guard, which exists to catch
    # exactly this).
    from services import ownership

    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setenv("WORKSHOP_SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("WORKSHOP_AUTH_ENV", str(tmp_path / "auth.env"))
    (tmp_path / "auth.env").write_text("CLAUDE_CODE_OAUTH_TOKEN=operator-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


@pytest.fixture
def single(monkeypatch):
    monkeypatch.setenv(mc.MODE_ENV, mc.MODE_SINGLE_OPERATOR)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv(mc.MODE_ENV, mc.MODE_MULTI_USER)


# ---- the rule ---------------------------------------------------------------


def test_multi_user_refuses_the_subscription_provider(hosted):
    """THE load-bearing test. Do not relax this to make a feature work."""
    with pytest.raises(mc.ProviderNotPermitted) as e:
        mc.resolve(principal_id=7, provider_id="claude_cli")
    assert "may only be used by the person who bought it" in str(e.value)


def test_single_operator_still_uses_the_subscription(single):
    cred = mc.resolve(principal_id=6, provider_id="claude_cli")
    assert cred.value == "operator-token"
    assert cred.origin == "deployment"


def test_a_subscription_credential_cannot_even_be_stored_when_hosted(hosted):
    with pytest.raises(mc.ProviderNotPermitted):
        mc.set_credential(7, "claude_cli", "someone-elses-token")


def test_hosted_deployments_do_not_offer_the_subscription(hosted):
    ids = {p["id"] for p in mc.available_providers()}
    assert "claude_cli" not in ids
    assert "anthropic_api" in ids


def test_single_operator_offers_everything(single):
    ids = {p["id"] for p in mc.available_providers()}
    assert "claude_cli" in ids


# ---- fail closed ------------------------------------------------------------


def test_unset_mode_is_single_operator(monkeypatch):
    monkeypatch.delenv(mc.MODE_ENV, raising=False)
    assert mc.deployment_mode() == mc.MODE_SINGLE_OPERATOR


@pytest.mark.parametrize("value", ["multi-user", "MULTI_USER ", "hosted", "sinlge_operator", "true"])
def test_an_unrecognised_mode_fails_closed_to_multi_user(monkeypatch, value):
    """A typo must not resolve to the permissive mode."""
    monkeypatch.setenv(mc.MODE_ENV, value)
    if value.strip().lower() in mc.MODES:
        return
    assert mc.deployment_mode() == mc.MODE_MULTI_USER


def test_mode_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(mc.MODE_ENV, "  MULTI_USER  ")
    assert mc.deployment_mode() == mc.MODE_MULTI_USER


def test_an_unknown_provider_raises_rather_than_defaulting():
    """A mistyped id must not silently route to the subscription."""
    with pytest.raises(ValueError):
        mp.get("claude_clii")


# ---- per-principal isolation ------------------------------------------------


def test_a_credential_belongs_to_one_principal(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-one")
    mc.set_credential(2, "anthropic_api", "sk-ant-two")
    assert mc.resolve(1, "anthropic_api").value == "sk-ant-one"
    assert mc.resolve(2, "anthropic_api").value == "sk-ant-two"


def test_a_principal_without_a_credential_is_not_given_someone_elses(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-one")
    with pytest.raises(mc.CredentialMissing):
        mc.resolve(2, "anthropic_api")


def test_clearing_one_principal_leaves_the_other(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-one")
    mc.set_credential(2, "anthropic_api", "sk-ant-two")
    mc.clear_credential(1, "anthropic_api")
    assert not mc.has_credential(1, "anthropic_api")
    assert mc.has_credential(2, "anthropic_api")


def test_providers_do_not_share_a_slot(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-x")
    mc.set_credential(1, "openai_api", "sk-openai-y")
    assert mc.resolve(1, "anthropic_api").value == "sk-ant-x"
    assert mc.resolve(1, "openai_api").value == "sk-openai-y"


def test_an_empty_credential_is_refused(hosted):
    with pytest.raises(ValueError):
        mc.set_credential(1, "anthropic_api", "   ")


# ---- the value never leaks --------------------------------------------------


def test_status_reports_presence_not_values(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-secret")
    rows = mc.status(1)
    assert "sk-ant-secret" not in repr(rows)
    assert [r for r in rows if r["id"] == "anthropic_api"][0]["stored"] is True


def test_repr_redacts_the_secret(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-secret")
    cred = mc.resolve(1, "anthropic_api")
    assert "sk-ant-secret" not in repr(cred)
    assert "redacted" in repr(cred)


def test_describe_carries_no_credential():
    assert "value" not in mp.describe(mp.CLAUDE_CLI)


# ---- as_env -----------------------------------------------------------------


def test_as_env_sets_only_this_providers_variable(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-x")
    env = mc.resolve(1, "anthropic_api").as_env({"PATH": "/usr/bin"})
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert env["PATH"] == "/usr/bin"


def test_as_env_strips_other_providers_credentials(hosted):
    """A child holding both an inactive subscription token and the API key it
    should use may quietly prefer the wrong one. That is how a hosted box would
    bill the operator's subscription for a stranger's turn."""
    mc.set_credential(1, "anthropic_api", "sk-ant-x")
    env = mc.resolve(1, "anthropic_api").as_env({
        "CLAUDE_CODE_OAUTH_TOKEN": "operator-token",
        "OPENAI_API_KEY": "sk-other",
    })
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-x"


# ---- provider catalog -------------------------------------------------------


def test_every_provider_declares_a_reason_when_it_is_single_user_only():
    for p in mp.all_providers():
        if not p.multi_user:
            assert p.restriction, f"{p.id} refuses multi-user without saying why"


def test_only_the_subscription_provider_is_single_user_only():
    single_only = {p.id for p in mp.all_providers() if not p.multi_user}
    assert single_only == {"claude_cli"}


def test_every_auth_mode_is_a_known_one():
    for p in mp.all_providers():
        assert p.auth_mode in mp.AUTH_MODES


def test_providers_have_distinct_env_vars():
    """Two providers sharing a variable would make as_env's strip incoherent."""
    seen = [p.env_var for p in mp.all_providers()]
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("provider_id,value,ok", [
    ("anthropic_api", "sk-ant-abc", True),
    ("anthropic_api", "sk-abc", False),
    ("anthropic_api", "https://example.com", False),
    ("openai_api", "sk-abc", True),
    ("anthropic_api", "", False),
    ("claude_cli", "anything", True),
])
def test_key_shape_check(provider_id, value, ok):
    assert mp.looks_like_key(provider_id, value) is ok


def test_runtime_ready_is_honest():
    """Only the CLI can actually run a turn today. Stage 4 changes this, and
    when it does the flag moves with the capability, not before it."""
    assert mp.CLAUDE_CLI.runtime_ready is True
    assert mp.ANTHROPIC_API.runtime_ready is False


# ---- today's behaviour is unchanged -----------------------------------------


def test_missing_subscription_token_says_what_to_do(single, tmp_path):
    (tmp_path / "auth.env").write_text("# empty\n")
    with pytest.raises(mc.CredentialMissing) as e:
        mc.resolve(6, "claude_cli")
    assert "claude auth login" in str(e.value)


def test_default_provider_is_the_cli():
    assert mp.DEFAULT_PROVIDER_ID == "claude_cli"


def test_resolve_defaults_to_the_default_provider(single):
    assert mc.resolve(6).provider_id == "claude_cli"


# ---- key_tail ---------------------------------------------------------------


def test_key_tail_returns_only_last_four_chars(hosted):
    mc.set_credential(1, "anthropic_api", "sk-ant-secret1234")
    tail = mc.key_tail(1, "anthropic_api")
    assert tail == "1234"
    assert len(tail) == 4
    assert "sk-ant" not in tail


def test_key_tail_absent_when_no_credential(hosted):
    assert mc.key_tail(1, "anthropic_api") is None


def test_list_endpoint_payload_carries_no_key_material(hosted):
    """The shape GET /model-providers serializes never contains a stored key value."""
    import json

    mc.set_credential(1, "anthropic_api", "sk-ant-verysecret9999")
    rows = mc.status(1)
    payload = [{**r, "last4": mc.key_tail(1, r["id"])} for r in rows]
    payload_str = json.dumps(payload)
    assert "sk-ant-verysecret9999" not in payload_str
    assert "verysecret" not in payload_str
    for row in payload:
        if row["last4"] is not None:
            assert len(row["last4"]) <= 4


# ---- the old path refuses too ----------------------------------------------


def test_the_running_subscription_path_still_works_today(single):
    """Nothing is switched over: single-operator mode is unchanged."""
    from services import llm_router
    env = llm_router._claude_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "operator-token"


def test_the_running_subscription_path_refuses_a_hosted_deployment(hosted):
    """Flipping the mode must not leave the OLD call path quietly serving
    everyone from the operator's account. The refusal lives where the token is
    actually spent, not only in the new seam."""
    from services import llm_router
    with pytest.raises(RuntimeError) as e:
        llm_router._claude_env()
    assert "may only be used by the person who bought it" in str(e.value)


# ---- the owner partition ----------------------------------------------------


def test_a_credential_lands_in_the_owners_partition(hosted, tmp_path):
    """Not the flat secrets directory, which per-project app secrets also use.

    A slug collision there would have handed a project a person's model key.
    """
    from services import secrets_store

    mc.set_credential(9, "anthropic_api", "sk-ant-nine")
    assert mc.credentials_path(9).exists()
    assert mc.credentials_path(9).parent.name == "9"
    assert not (secrets_store._root() / "principal:9.json").exists()


def test_a_credential_file_is_not_world_readable(hosted):
    import stat

    mc.set_credential(9, "anthropic_api", "sk-ant-nine")
    mode = stat.S_IMODE(mc.credentials_path(9).stat().st_mode)
    assert mode == 0o600


def test_a_legacy_flat_store_is_adopted_on_first_read(hosted):
    """A process that did not run the boot migration must not see an empty
    store. Every read adopts, so a CLI, a worker and a test all agree."""
    from services import secrets_store

    secrets_store.set_values("principal:4", {"anthropic_api.credential": "sk-ant-four"})
    assert mc.resolve(4, "anthropic_api").value == "sk-ant-four"
    assert not (secrets_store._root() / "principal:4.json").exists()
    assert mc.credentials_path(4).exists()


def test_migration_leaves_a_partitioned_store_alone(hosted):
    """Two versions of a secret are never merged: merging would resurrect a key
    the operator removed."""
    from services import secrets_store

    mc.set_credential(5, "anthropic_api", "sk-ant-current")
    secrets_store.set_values("principal:5", {"anthropic_api.credential": "sk-ant-stale"})
    mc.migrate_legacy_files()
    assert mc.resolve(5, "anthropic_api").value == "sk-ant-current"


def test_migration_ignores_a_file_that_is_not_a_principal(hosted):
    from services import secrets_store

    secrets_store.set_values("principal:notanumber", {"A": "1"})
    assert mc.migrate_legacy_files() == 0
    assert secrets_store.load("principal:notanumber") == {"A": "1"}


# ---- the KEK is mandatory when hosted ---------------------------------------


def test_multi_user_refuses_to_start_with_a_plaintext_store(hosted, monkeypatch):
    from services import secrets_crypto

    monkeypatch.delenv(secrets_crypto.KEK_ENV, raising=False)
    with pytest.raises(RuntimeError) as e:
        mc.require_encryption_in_multi_user()
    assert secrets_crypto.KEK_ENV in str(e.value)


def test_multi_user_starts_once_a_kek_is_configured(hosted, monkeypatch):
    from services import secrets_crypto

    monkeypatch.setenv(secrets_crypto.KEK_ENV, secrets_crypto.generate_kek())
    mc.require_encryption_in_multi_user()


def test_single_operator_gains_no_new_requirement(single, monkeypatch):
    from services import secrets_crypto

    monkeypatch.delenv(secrets_crypto.KEK_ENV, raising=False)
    mc.require_encryption_in_multi_user()


# ---- the subscription is settable, and a turn picks it up -------------------


def test_a_stored_subscription_token_beats_the_auth_file(single):
    """The whole point of the surface. If the file won, a paste in the UI would
    appear to succeed and change nothing."""
    mc.set_credential(6, "claude_cli", "token-from-the-app")
    cred = mc.resolve(6, "claude_cli")
    assert cred.value == "token-from-the-app"
    assert cred.origin == "principal"


def test_the_auth_file_still_serves_a_deployment_that_never_used_the_screen(single):
    assert mc.resolve(6, "claude_cli").value == "operator-token"
    assert mc.resolve(6, "claude_cli").origin == "deployment"


def test_the_turn_path_spends_the_token_set_in_the_app(single, monkeypatch):
    """Read per call, so there is no restart between Save and the next turn."""
    from services import auth_store, llm_router

    monkeypatch.setattr(auth_store, "operator_id", lambda: 6)
    assert llm_router._claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "operator-token"
    mc.set_credential(6, "claude_cli", "token-from-the-app")
    assert llm_router._claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "token-from-the-app"
    mc.clear_credential(6, "claude_cli")
    assert llm_router._claude_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "operator-token"


def test_a_stored_api_key_never_reaches_the_turn_path(single, monkeypatch):
    """THE STANDING RULE for this installation: subscription auth only, never
    ANTHROPIC_API_KEY. Storing an Anthropic key must not create a second
    credential path into `claude -p`."""
    from services import auth_store, llm_router

    monkeypatch.setattr(auth_store, "operator_id", lambda: 6)
    mc.set_credential(6, "anthropic_api", "sk-ant-should-never-be-spent")
    env = llm_router._claude_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "sk-ant-should-never-be-spent" not in repr(env)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "operator-token"


def test_an_unpinned_call_in_multi_user_gets_no_principal(hosted):
    """Never "whoever set this box up": that fallback is how one person's
    subscription would serve another's turn."""
    from services import ownership

    ownership.clear_current_owner()
    assert mc.current_principal() is None


def test_a_pinned_owner_is_the_principal(single):
    from services import ownership

    ownership.set_current_owner(11)
    try:
        assert mc.current_principal() == 11
    finally:
        ownership.clear_current_owner()


# ---- status tells the truth about what can run a turn -----------------------


def _row(rows, pid):
    return next(r for r in rows if r["id"] == pid)


def test_a_stored_key_is_not_reported_usable_while_nothing_routes_to_it(hosted):
    """`stored` and `usable` are different questions. They used to be one
    boolean, so a key the runtime cannot spend displayed exactly like the
    subscription serving every turn."""
    mc.set_credential(1, "anthropic_api", "sk-ant-x")
    row = _row(mc.status(1), "anthropic_api")
    assert row["stored"] is True
    assert row["configured"] is True
    assert row["usable"] is False  # runtime_ready is False for API providers
    assert row["source"] == "principal"


def test_status_says_where_a_configured_credential_came_from(single):
    assert _row(mc.status(6), "claude_cli")["source"] == "deployment"
    mc.set_credential(6, "claude_cli", "token-from-the-app")
    assert _row(mc.status(6), "claude_cli")["source"] == "principal"


def test_an_unconfigured_provider_has_no_source(hosted):
    row = _row(mc.status(1), "anthropic_api")
    assert row["configured"] is False
    assert row["usable"] is False
    assert row["source"] is None


def test_status_still_carries_no_values(single):
    import json

    mc.set_credential(6, "claude_cli", "token-from-the-app-9999")
    assert "token-from-the-app" not in json.dumps(mc.status(6))


# ---- the boot check cannot lock the operator out ----------------------------


@pytest.mark.parametrize("value", ["", "multi-user", "MULTI_USER_X", "hosted",
                                   "sinlge_operator", "true", "single_operator"])
def test_only_a_declared_multi_user_can_refuse_to_boot(monkeypatch, value):
    """A typo must not stop a single-operator installation from starting.

    `deployment_mode()` fails closed, so several of these resolve to multi-user
    for credential decisions, which is right. The boot refusal asks the narrower
    question, because its failure mode is the whole product rather than a turn.
    """
    from services import secrets_crypto

    monkeypatch.delenv(secrets_crypto.KEK_ENV, raising=False)
    if value:
        monkeypatch.setenv(mc.MODE_ENV, value)
    else:
        monkeypatch.delenv(mc.MODE_ENV, raising=False)
    mc.require_encryption_in_multi_user()


def test_a_declared_multi_user_still_refuses(monkeypatch):
    from services import secrets_crypto

    monkeypatch.delenv(secrets_crypto.KEK_ENV, raising=False)
    monkeypatch.setenv(mc.MODE_ENV, "multi_user")
    with pytest.raises(RuntimeError):
        mc.require_encryption_in_multi_user()


def test_a_typo_still_refuses_a_credential_even_though_it_boots(monkeypatch):
    """The fail-closed reading is unchanged where it protects an account."""
    monkeypatch.setenv(mc.MODE_ENV, "multi-user")
    assert mc.is_multi_user() is True
    assert mc.multi_user_is_declared() is False
    with pytest.raises(mc.ProviderNotPermitted):
        mc.resolve(principal_id=7, provider_id="claude_cli")
