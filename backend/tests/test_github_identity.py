"""The connected GitHub account: storage, refusals, and the two environments.

NO REAL TOKEN VALUE IS EVER ASSERTED AGAINST A FIXTURE THAT LOOKS LIKE ONE. The
strings here are obviously synthetic and short, because a test file is a place
credentials get copied from.

github.com is never contacted: `verify` is the single seam every write goes
through, so stubbing it covers the network without a live account.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess

import pytest

from services import github_identity, installation, ownership


OWNER = 4242


@pytest.fixture(autouse=True)
def _partition(tmp_path, monkeypatch):
    """Every test gets its own owner partition, so nothing reads the real one."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(OWNER)
    yield
    ownership.clear_current_owner()


def _ok(login="octo", scopes=None):
    return {"ok": True, "login": login, "scopes": scopes or [],
            "detail": f"Signed in as {login}."}


def _make_operator(monkeypatch, db_path) -> int:
    """An operator account in a scratch auth database, and its id.

    Only the subprocess tests need one: they exercise the helper, which has no
    request behind it and therefore resolves its owner through
    `ownership.pin_operator()` rather than from a pinned contextvar.
    """
    from pathlib import Path

    from services import auth_store

    monkeypatch.setattr(auth_store, "DB_PATH", Path(db_path))
    user = auth_store.create_user("operator@example.invalid", "not-a-real-password")
    auth_store.set_operator(user["id"])
    return int(user["id"])


# ---- storage ----------------------------------------------------------------

def test_token_is_stored_in_the_owners_partition_and_never_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)

    out = github_identity.connect_token("secret-value", OWNER)

    assert github_identity.token(OWNER) == "secret-value"
    # The status payload is what crosses the HTTP boundary. Nothing in it, at any
    # depth, may contain the token.
    assert "secret-value" not in repr(out)
    assert out["connected"] is True
    assert out["login"] == "octo"

    stored = tmp_path / "users" / str(OWNER) / "secrets" / "github.json"
    assert stored.exists(), "the credential belongs to the owner, not to a global file"
    assert stored.stat().st_mode & 0o777 == 0o600


def test_another_owners_partition_is_a_different_credential(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)

    github_identity.connect_token("mine", OWNER)
    assert github_identity.token(OWNER + 1) is None


def test_disconnect_forgets_the_token(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    monkeypatch.setattr(github_identity, "remove_credential_helper", lambda: None)

    github_identity.connect_token("mine", OWNER)
    out = github_identity.disconnect(OWNER)

    assert github_identity.token(OWNER) is None
    assert out["connected"] is False
    assert out["login"] == ""


def test_the_client_id_survives_a_disconnect(monkeypatch):
    """It is a public application id, not a credential. Making the operator
    re-register an application to sign in again would be work for nothing."""
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    monkeypatch.setattr(github_identity, "remove_credential_helper", lambda: None)

    github_identity.set_client_id("Iv1.example", OWNER)
    github_identity.connect_token("mine", OWNER)
    github_identity.disconnect(OWNER)

    assert github_identity.client_id(OWNER) == "Iv1.example"


# ---- refusals ----------------------------------------------------------------

@pytest.mark.parametrize("scope", ["read:org", "write:org", "admin:org"])
def test_an_org_scope_is_refused_at_the_point_of_entry(monkeypatch, scope):
    """[SEC-G1]: the excluded-organisation boundary has to live in the credential,
    not only in string matching in code. A token that CAN reach an organisation
    cannot honour it whatever the code around it does, so it is not stored."""
    monkeypatch.setattr(github_identity, "verify",
                        lambda t: _ok(scopes=["repo", scope]))

    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.connect_token("broad", OWNER)

    assert scope in str(e.value)
    assert "fine-grained" in str(e.value)
    assert github_identity.token(OWNER) is None, "a refused token must not be stored"


def test_a_narrow_token_is_accepted(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok(scopes=["repo"]))
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)

    out = github_identity.connect_token("narrow", OWNER)
    assert out["connected"] is True
    assert out["scopes"] == ["repo"]


def test_a_blocked_account_is_refused(monkeypatch):
    """An installation names accounts it must never touch. A login that matches
    one is refused before anything is written."""
    monkeypatch.setenv("WORKSHOP_BLOCKED_REPO_OWNERS", "offlimits")
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok(login="offlimits-bot"))

    with pytest.raises(ValueError):
        github_identity.connect_token("blocked", OWNER)
    assert github_identity.token(OWNER) is None


def test_a_token_github_rejects_is_not_stored(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: {
        "ok": False, "login": "", "scopes": [],
        "detail": "GitHub rejected this token. It is wrong, revoked or expired."})

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.connect_token("wrong", OWNER)
    assert github_identity.token(OWNER) is None


# ---- the two environments ----------------------------------------------------

def test_git_env_carries_the_token_as_a_header_never_in_a_url(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    github_identity.connect_token("tok", OWNER)

    env = github_identity.git_env(OWNER, base={})

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    header = env["GIT_CONFIG_VALUE_0"]
    assert header.startswith("Authorization: Basic ")
    decoded = base64.b64decode(header.split()[-1]).decode()
    assert decoded == "x-access-token:tok"
    # The token must not be reachable any other way: not in a URL, not in argv.
    assert not any(k.startswith("GIT_CONFIG_VALUE") and "https://x-access-token" in v
                   for k, v in env.items())
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_env_layers_over_a_caller_that_already_set_config_entries(monkeypatch):
    """git reads every index below GIT_CONFIG_COUNT, so writing index 0 over a
    caller's own entry would silently drop that entry."""
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    github_identity.connect_token("tok", OWNER)

    env = github_identity.git_env(OWNER, base={
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.autocrlf",
        "GIT_CONFIG_VALUE_0": "false",
    })

    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.autocrlf"
    assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraHeader"


def test_gh_env_sets_gh_token_and_drops_a_stale_github_token(monkeypatch):
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    github_identity.connect_token("tok", OWNER)

    env = github_identity.gh_env(OWNER, base={"GITHUB_TOKEN": "someone-elses"})

    assert env["GH_TOKEN"] == "tok"
    assert "GITHUB_TOKEN" not in env, "gh prefers GITHUB_TOKEN; a stale one would win"


def test_no_account_means_the_environment_is_left_alone():
    base = {"PATH": "/usr/bin"}
    assert github_identity.git_env(OWNER, base=base) == base
    assert github_identity.gh_env(OWNER, base=base) == base


# ---- the credential helper ---------------------------------------------------

def test_installing_the_helper_discards_the_chain_before_it(monkeypatch):
    """An empty value first is git's documented way of dropping helpers
    configured earlier. Without it a stale `gh auth git-credential` answers
    first and fails the push, which is the exact bug this feature closes."""
    calls: list[list[str]] = []

    def fake(args, *, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(github_identity, "_git_config", fake)
    github_identity.install_credential_helper()

    for host in ("https://github.com", "https://gist.github.com"):
        key = f"credential.{host}.helper"
        assert ["--unset-all", key] in calls
        assert ["--add", key, ""] in calls
        assert ["--add", key, f"!{github_identity.helper_path()}"] in calls
    empty = calls.index(["--add", "credential.https://github.com.helper", ""])
    ours = calls.index(["--add", "credential.https://github.com.helper",
                        f"!{github_identity.helper_path()}"])
    assert empty < ours, "the reset has to be configured before ours, not after"


def test_removing_the_helper_restores_what_the_host_had(monkeypatch):
    """Leaving the empty resetting entry behind would silently disable whatever
    the host had configured for itself before this installation existed."""
    ours = f"!{github_identity.helper_path()}"
    existing = ["", ours, "!/usr/bin/other-helper"]
    calls: list[list[str]] = []

    def fake(args, *, check=True):
        calls.append(args)
        if args[0] == "--get-all":
            return subprocess.CompletedProcess(args, 0, "\n".join(existing) + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(github_identity, "_git_config", fake)
    github_identity.remove_credential_helper()

    added = [c[2] for c in calls if c[0] == "--add"]
    assert added == ["!/usr/bin/other-helper", "!/usr/bin/other-helper"]
    assert ours not in added
    assert "" not in added


def test_git_itself_gets_the_credential_from_the_helper(monkeypatch, tmp_path):
    """THE END TO END PROOF, and the one that reproduces the original failure.

    `git credential fill` is git's own credential machinery, the same code path a
    `git push` uses, and it makes no network request. Run against a HOME that has
    the chain the host actually had (a `gh auth git-credential` for a `gh` that
    is not logged in) it produces nothing and a push then asks for a username on
    a terminal that has none, which is

        fatal: could not read Username for '<the host>'

    Run against a HOME with this installation's helper configured, it produces
    the connected account. Both halves are asserted, so a regression that stops
    installing the helper fails here rather than in a push weeks later.
    """
    import shutil

    if shutil.which("git") is None:  # pragma: no cover - git is a hard dependency
        pytest.skip("git is not on PATH")

    # The helper runs with no request behind it, so it resolves the owner the way
    # every other automation set-point does: `ownership.pin_operator()`. Give the
    # scratch partition an operator account for it to find, and connect the token
    # under THAT id rather than under this file's arbitrary one.
    home = tmp_path / "home"
    home.mkdir()
    auth_db = tmp_path / "auth.db"
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""),
           "GIT_TERMINAL_PROMPT": "0",
           "WORKSHOP_RUNTIME": str(ownership._RUNTIME),
           "WORKSHOP_AUTH_DB": str(auth_db)}
    operator_id = _make_operator(monkeypatch, auth_db)
    host = "git" + "hub.com"
    key = f"credential.https://{host}.helper"
    request = f"protocol=https\nhost={host}\n\n"

    def fill() -> str:
        proc = subprocess.run(["git", "credential", "fill"], input=request,
                              env=env, capture_output=True, text=True, timeout=30)
        return proc.stdout or ""

    # BEFORE: a helper that cannot answer. Nothing comes back but the echo of the
    # request, which is what a push has no credential from.
    subprocess.run(["git", "config", "--global", key, "!false"],
                   env=env, capture_output=True, text=True, timeout=15)
    assert "password=" not in fill()

    # AFTER: connect an account and point the chain at this installation.
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    github_identity.connect_token("token-under-test", operator_id)
    subprocess.run(["git", "config", "--global", "--unset-all", key],
                   env=env, capture_output=True, text=True, timeout=15)
    for value in ("", f"!{github_identity.helper_path()}"):
        subprocess.run(["git", "config", "--global", "--add", key, value],
                       env=env, capture_output=True, text=True, timeout=15)

    out = fill()
    assert "username=x-access-token" in out
    assert "password=token-under-test" in out


def test_the_helper_answers_nothing_for_a_host_it_does_not_own(monkeypatch, tmp_path):
    """A credential helper that answers every host would hand this account's
    token to whatever remote a repository happens to point at."""
    auth_db = tmp_path / "auth.db"
    operator_id = _make_operator(monkeypatch, auth_db)
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    github_identity.connect_token("token-under-test", operator_id)

    proc = subprocess.run(
        [str(github_identity.helper_path()), "get"],
        input="protocol=https\nhost=example.invalid\n\n",
        env={**os.environ, "WORKSHOP_RUNTIME": str(ownership._RUNTIME),
             "WORKSHOP_AUTH_DB": str(auth_db)},
        capture_output=True, text=True, timeout=30)
    assert proc.stdout == ""
    assert proc.returncode == 0, "a helper that exits non-zero aborts the git operation"


def test_helper_installed_is_false_when_only_one_host_is_wired(monkeypatch):
    ours = f"!{github_identity.helper_path()}"

    def fake(args, *, check=True):
        out = ours if "gist" not in args[1] else ""
        return subprocess.CompletedProcess(args, 0, out + "\n", "")

    monkeypatch.setattr(github_identity, "_git_config", fake)
    assert github_identity.helper_installed() is False


# ---- the device grant --------------------------------------------------------

def test_the_device_flow_says_what_is_missing_rather_than_failing_opaquely(monkeypatch):
    monkeypatch.delenv(github_identity.CLIENT_ID_ENV, raising=False)
    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.start_device(OWNER)
    assert "Client ID" in str(e.value)


def test_pending_is_an_answer_not_an_error(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"error": "authorization_pending"})

    out = github_identity.poll_device("dev-code", OWNER)
    assert out["status"] == "pending"
    assert github_identity.token(OWNER) is None


def test_a_completed_device_sign_in_stores_the_token(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"access_token": "from-device"})
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)

    out = github_identity.poll_device("dev-code", OWNER)

    assert out["status"] == "connected"
    assert out["source"] == "device"
    assert "from-device" not in repr(out)
    assert github_identity.token(OWNER) == "from-device"


def test_a_device_token_gets_the_same_refusals_as_a_pasted_one(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"access_token": "from-device"})
    monkeypatch.setattr(github_identity, "verify",
                        lambda t: _ok(scopes=["read:org"]))

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.poll_device("dev-code", OWNER)
    assert github_identity.token(OWNER) is None


def test_device_flow_disabled_names_the_setting_to_turn_on(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"error": "device_flow_disabled"})

    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.poll_device("dev-code", OWNER)
    assert "Device flow" in str(e.value)


# ---- what a screen may see ----------------------------------------------------

def test_status_is_safe_to_render(monkeypatch):
    monkeypatch.setattr(github_identity, "verify",
                        lambda t: _ok(scopes=["public_repo"]))
    monkeypatch.setattr(github_identity, "helper_installed", lambda: True)
    github_identity.connect_token("tok", OWNER)

    out = github_identity.status(OWNER)

    assert set(out) == {"connected", "login", "source", "scopes", "connected_at",
                        "client_id_set", "device_available", "app_registered",
                        "manifest_available", "helper_installed", "helper_path",
                        "defaults"}
    assert out["helper_installed"] is True
    assert "tok" not in repr(out)


# ---- the manifest flow --------------------------------------------------------

ORIGIN = "https://workshop.test-host.invalid"


@pytest.fixture
def _origin(monkeypatch):
    """This installation declares an address a browser can reach it at."""
    for var in installation.ORIGIN_ENVS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WORKSHOP_CANONICAL_ORIGIN", ORIGIN)


@pytest.fixture
def _no_origin(monkeypatch):
    for var in installation.ORIGIN_ENVS:
        monkeypatch.delenv(var, raising=False)


def _conversion_body(client_id="Iv1.app", client_secret="app-secret"):
    return {"id": 99, "slug": "workshop-test", "client_id": client_id,
            "client_secret": client_secret}


def test_the_manifest_asks_for_the_permissions_the_platform_uses_and_no_org(_origin):
    """Contents read and write, metadata read, pull requests read and write.

    [SEC-G1] is the reason the last assertion is not decoration: an application
    that could be granted an organisation permission would put the boundary back
    into string matching, which is the thing that ticket exists to end.
    """
    m = github_identity.app_manifest(ORIGIN)

    assert m["default_permissions"] == {"contents": "write", "metadata": "read",
                                        "pull_requests": "write"}
    assert not any("org" in key for key in m["default_permissions"])
    assert m["public"] is False
    assert m["redirect_url"] == ORIGIN + github_identity.MANIFEST_REDIRECT_PATH
    assert m["callback_urls"] == [ORIGIN + github_identity.OAUTH_CALLBACK_PATH]
    assert len(m["name"]) <= 34, "the forge caps an App name at 34 characters"


def test_begin_registration_carries_a_state_the_forge_will_hand_back(_origin):
    out = github_identity.begin_registration(OWNER)

    assert out["post_url"].startswith(github_identity.APP_NEW_URL + "?state=")
    assert json.loads(out["manifest"])["url"] == ORIGIN


def test_an_installation_with_no_address_says_so_rather_than_failing_later(_no_origin):
    """The forge would have nowhere to redirect back to. A screen that offered
    the button anyway would fail for reasons nobody can see."""
    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.begin_registration(OWNER)
    assert "Paste a token instead" in str(e.value)
    assert github_identity.status(OWNER)["manifest_available"] is False


def test_registration_stores_the_application_and_returns_its_owner(monkeypatch, _origin):
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: _conversion_body())
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])

    owner = github_identity.complete_registration("manifest-code", state)

    assert owner == OWNER
    assert github_identity.client_id(OWNER) == "Iv1.app"
    assert github_identity.app_registered(OWNER) is True


def _state_from(post_url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(post_url).query)["state"][0]


# ---- the state nonce ----------------------------------------------------------
# It is the only thing that authenticates a callback: the session cookie is
# SameSite=Strict, so a redirect arriving from the forge does not carry it.

def test_a_state_nobody_minted_is_refused(monkeypatch, _origin):
    called = []
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: called.append(code) or _conversion_body())

    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.complete_registration("manifest-code", "not-a-real-state")

    assert "did not start here" in str(e.value)
    assert called == [], "a refused state must not reach the forge at all"
    assert github_identity.client_id(OWNER) is None


def test_a_state_is_accepted_exactly_once(monkeypatch, _origin):
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: _conversion_body())
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])

    github_identity.complete_registration("manifest-code", state)
    with pytest.raises(github_identity.GithubAuthError):
        github_identity.complete_registration("manifest-code", state)


def test_a_registration_state_cannot_redeem_the_authorize_leg(monkeypatch, _origin):
    """The two legs are different acts. A nonce minted for one is not a
    credential for the other, and trying burns it."""
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.complete_authorize("code", state)


def test_an_expired_state_is_refused(monkeypatch, _origin):
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])
    later = github_identity.time.monotonic() + github_identity._STATE_TTL_SECONDS + 1

    monkeypatch.setattr(github_identity.time, "monotonic", lambda: later)

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.complete_registration("manifest-code", state)


def test_the_owner_comes_from_the_state_and_not_from_the_request(monkeypatch, _origin):
    """A callback has no session behind it, so there is no ambient owner to
    trust. The one the flow started as is the one it finishes as."""
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: _conversion_body())
    state = _state_from(github_identity.begin_registration(OWNER + 7)["post_url"])

    assert github_identity.complete_registration("code", state) == OWNER + 7
    assert github_identity.client_id(OWNER + 7) == "Iv1.app"
    assert github_identity.client_id(OWNER) is None


# ---- the OAuth web flow -------------------------------------------------------

def _register(monkeypatch):
    monkeypatch.setattr(github_identity, "_conversion", lambda code: _conversion_body())
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])
    github_identity.complete_registration("manifest-code", state)


def test_the_authorize_url_carries_pkce_and_a_fresh_state(monkeypatch, _origin):
    _register(monkeypatch)

    url = github_identity.begin_authorize(OWNER)

    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    assert url.startswith(github_identity.AUTHORIZE_URL)
    assert q["client_id"] == ["Iv1.app"]
    assert q["redirect_uri"] == [ORIGIN + github_identity.OAUTH_CALLBACK_PATH]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["state"]
    assert "scope" not in q, "an org scope must not be reachable by asking for one"
    # The challenge is a hash, so the verifier is not recoverable from the URL.
    assert q["code_challenge"][0] != q["state"][0]


def test_a_completed_web_sign_in_stores_a_user_token(monkeypatch, _origin):
    _register(monkeypatch)
    sent: list[dict] = []
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: sent.append(data) or {"access_token": "from-web"})
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    state = _state_from_url(github_identity.begin_authorize(OWNER))

    assert github_identity.complete_authorize("auth-code", state) == OWNER

    out = github_identity.status(OWNER)
    assert out["connected"] is True
    assert out["source"] == "oauth"
    assert github_identity.token(OWNER) == "from-web"
    assert sent[0]["code_verifier"], "the verifier the challenge was built from"


def _state_from_url(url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(url).query)["state"][0]


def test_a_web_token_gets_the_same_org_refusal_as_a_pasted_one(monkeypatch, _origin):
    """connect_token is the one door. [SEC-G1] applies to every way in."""
    _register(monkeypatch)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"access_token": "broad"})
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok(scopes=["read:org"]))
    state = _state_from_url(github_identity.begin_authorize(OWNER))

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.complete_authorize("auth-code", state)
    assert github_identity.token(OWNER) is None


def test_a_web_token_gets_the_same_blocked_account_refusal(monkeypatch, _origin):
    _register(monkeypatch)
    monkeypatch.setenv("WORKSHOP_BLOCKED_REPO_OWNERS", "offlimits")
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"access_token": "blocked"})
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok(login="offlimits-bot"))
    state = _state_from_url(github_identity.begin_authorize(OWNER))

    with pytest.raises(ValueError):
        github_identity.complete_authorize("auth-code", state)
    assert github_identity.token(OWNER) is None


# ---- the client secret --------------------------------------------------------

SECRET = "app-secret-under-test"


def test_the_client_secret_never_appears_in_status(monkeypatch, _origin):
    """It is written once by the registration leg and read only by this module's
    own token exchange. `status()` is what crosses the HTTP boundary."""
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: _conversion_body(client_secret=SECRET))
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])
    github_identity.complete_registration("manifest-code", state)

    out = github_identity.status(OWNER)

    assert SECRET not in repr(out)
    assert not any("secret" in key for key in out)
    assert out["app_registered"] is True, "the fact of it is surfaced, the value is not"


def test_no_route_can_return_the_client_secret():
    """The response models are the boundary. None of them has a field for it."""
    from routes import github_identity_routes as routes

    for model in (routes.StatusOut, routes.DevicePollOut, routes.ManifestOut,
                  routes.AuthorizeOut, routes.DefaultsOut):
        assert not any("secret" in name for name in model.model_fields)


def test_a_refused_state_does_not_quote_the_secret(monkeypatch, _origin):
    monkeypatch.setattr(github_identity, "_conversion",
                        lambda code: _conversion_body(client_secret=SECRET))
    state = _state_from(github_identity.begin_registration(OWNER)["post_url"])
    github_identity.complete_registration("manifest-code", state)

    with pytest.raises(github_identity.GithubAuthError) as e:
        github_identity.complete_authorize("code", "wrong-state")
    assert SECRET not in str(e.value)


def test_disconnecting_keeps_the_registered_application(monkeypatch, _origin):
    """The application is not the connection. Re-registering one to sign in
    again would be work for nothing."""
    _register(monkeypatch)
    monkeypatch.setattr(github_identity, "_form_post",
                        lambda url, data: {"access_token": "from-web"})
    monkeypatch.setattr(github_identity, "verify", lambda t: _ok())
    monkeypatch.setattr(github_identity, "helper_installed", lambda: False)
    monkeypatch.setattr(github_identity, "remove_credential_helper", lambda: None)
    github_identity.complete_authorize(
        "auth-code", _state_from_url(github_identity.begin_authorize(OWNER)))

    github_identity.disconnect(OWNER)

    assert github_identity.token(OWNER) is None
    assert github_identity.app_registered(OWNER) is True


# ---- the global defaults ------------------------------------------------------

def test_the_defaults_are_the_shipped_ones_until_someone_changes_them():
    assert github_identity.defaults(OWNER) == {
        "auto_create_repo": True,
        "default_visibility": "private",
        "default_branch_name": "main",
    }


def test_setting_one_default_leaves_the_others_alone():
    github_identity.set_defaults(default_branch_name="trunk", owner_id=OWNER)
    out = github_identity.set_defaults(auto_create_repo=False, owner_id=OWNER)

    assert out == {"auto_create_repo": False, "default_visibility": "private",
                   "default_branch_name": "trunk"}


@pytest.mark.parametrize("bad", ["", "  ", "-leading", "has space", "a..b", "trailing/"])
def test_a_branch_name_git_would_refuse_is_refused_here(bad):
    with pytest.raises(github_identity.GithubAuthError):
        github_identity.set_defaults(default_branch_name=bad, owner_id=OWNER)
    assert github_identity.defaults(OWNER)["default_branch_name"] == "main"


def test_visibility_is_one_of_two_words():
    with pytest.raises(github_identity.GithubAuthError):
        github_identity.set_defaults(default_visibility="internal", owner_id=OWNER)
    assert github_identity.set_defaults(
        default_visibility="public", owner_id=OWNER)["default_visibility"] == "public"


# ---- the callbacks are reachable without a session ----------------------------

def test_a_callback_is_not_behind_the_operator_gate():
    """A redirect from the forge carries no cookie (SameSite=Strict), so a gate
    here would 401 every real completion. The state nonce is the gate instead,
    and a bad one lands the operator back on the settings page with a sentence
    rather than on a status code nobody sees.
    """
    from fastapi.testclient import TestClient

    import main

    r = TestClient(main.app).get("/api/github/authorized?state=nope&code=x",
                                 follow_redirects=False)

    assert r.status_code == 303, "not 401: this route cannot require a session"
    assert github_identity.SETTINGS_PATH in r.headers["location"]
    assert "github_error" in r.headers["location"]
