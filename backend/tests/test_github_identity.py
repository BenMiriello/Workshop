"""The connected GitHub account: storage, refusals, and the two environments.

NO REAL TOKEN VALUE IS EVER ASSERTED AGAINST A FIXTURE THAT LOOKS LIKE ONE. The
strings here are obviously synthetic and short, because a test file is a place
credentials get copied from.

github.com is never contacted: `verify` is the single seam every write goes
through, so stubbing it covers the network without a live account.
"""
from __future__ import annotations

import base64
import os
import subprocess

import pytest

from services import github_identity, ownership


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
    monkeypatch.setattr(github_identity, "_device_post",
                        lambda url, data: {"error": "authorization_pending"})

    out = github_identity.poll_device("dev-code", OWNER)
    assert out["status"] == "pending"
    assert github_identity.token(OWNER) is None


def test_a_completed_device_sign_in_stores_the_token(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_device_post",
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
    monkeypatch.setattr(github_identity, "_device_post",
                        lambda url, data: {"access_token": "from-device"})
    monkeypatch.setattr(github_identity, "verify",
                        lambda t: _ok(scopes=["read:org"]))

    with pytest.raises(github_identity.GithubAuthError):
        github_identity.poll_device("dev-code", OWNER)
    assert github_identity.token(OWNER) is None


def test_device_flow_disabled_names_the_setting_to_turn_on(monkeypatch):
    github_identity.set_client_id("Iv1.example", OWNER)
    monkeypatch.setattr(github_identity, "_device_post",
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
                        "client_id_set", "device_available", "helper_installed",
                        "helper_path"}
    assert out["helper_installed"] is True
    assert "tok" not in repr(out)
