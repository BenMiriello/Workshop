"""The credential probes, against the output the real CLIs actually produce.

Every `raw` string here was captured from the CLIs on a real host and then had
its account details replaced with placeholders. The shapes — which stream, which
glyph, which key — are measured, not imagined, because the first version of this
parser guessed `✗` where `gh` prints an ASCII `X` and guessed stdout where this
`gh` writes to stderr.

The test that matters most is `test_claude_probe_never_claims_verified`: the
whole point of the module is that a configured Claude token is not a working
one, and a future edit that "simplifies" the two probes into one would take that
distinction out.
"""
from __future__ import annotations

import asyncio

from services import auth_health
from services.mcp_health import KIND_DANGER, KIND_OK, KIND_STOPPED, KIND_WARN


GH_OK = """github.com
  ✓ Logged in to github.com as octocat (/home/u/.config/gh/hosts.yml)
  ✓ Git operations for github.com configured to use https protocol.
  ✓ Token: ghp_************************************
  ✓ Token scopes: read:org, repo
"""

GH_EXPIRED = """github.com
  X github.com: authentication failed
  - The github.com token in GH_TOKEN is no longer valid.
"""

GH_LOGGED_OUT = "You are not logged into any GitHub hosts. Run gh auth login to authenticate.\n"


def test_gh_signed_in():
    row = auth_health.parse_gh_auth_status(GH_OK, 0)
    assert row["kind"] == KIND_OK
    assert row["state"] == "Signed in"
    assert row["account"] == "octocat"
    assert row["scopes"] == ["read:org", "repo"]


def test_gh_expired_token_keeps_the_actionable_sentence():
    row = auth_health.parse_gh_auth_status(GH_EXPIRED, 1)
    assert row["kind"] == KIND_DANGER
    # The reason names the env var to fix, which is the only useful part.
    assert "no longer valid" in row["detail"]
    assert "GH_TOKEN" in row["detail"]


def test_gh_logged_out():
    row = auth_health.parse_gh_auth_status(GH_LOGGED_OUT, 1)
    assert row["kind"] == KIND_DANGER
    assert "not logged into any GitHub hosts" in row["detail"]


def test_gh_never_echoes_a_token():
    """A token line is dropped rather than trusted to stay masked."""
    leaky = GH_OK.replace("ghp_************************************", "ghp_realsecretvalue")
    row = auth_health.parse_gh_auth_status(leaky, 0)
    assert "realsecretvalue" not in repr(row)


def test_gh_success_with_unreadable_output_is_unknown_not_ok():
    """Exit 0 that we could not parse must not render as a green tick."""
    row = auth_health.parse_gh_auth_status("something we have never seen\n", 0)
    assert row["kind"] == KIND_STOPPED
    assert row["state"] == "Unknown"


def test_gh_parser_is_total():
    for raw in ("", "\n", "   ", "\x1b[31mgarbage\x1b[0m", "no glyphs at all"):
        for rc in (0, 1, 128):
            row = auth_health.parse_gh_auth_status(raw, rc)
            assert row["kind"] in (KIND_OK, KIND_WARN, KIND_DANGER, KIND_STOPPED)
            assert row["detail"] is not None


CLAUDE_SUBSCRIPTION = """{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "email": "user@example.com",
  "orgName": "user@example.com's Organization",
  "subscriptionType": "max"
}"""

CLAUDE_OAUTH_TOKEN = """{
  "loggedIn": true,
  "authMethod": "oauth_token",
  "apiProvider": "firstParty"
}"""

CLAUDE_API_KEY = """{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "apiKeySource": "ANTHROPIC_API_KEY",
  "email": null,
  "subscriptionType": null
}"""

CLAUDE_LOGGED_OUT = """{
  "loggedIn": false,
  "authMethod": "none",
  "apiProvider": "firstParty"
}"""


def test_claude_subscription():
    row = auth_health.parse_claude_auth_status(CLAUDE_SUBSCRIPTION, 0)
    assert row["kind"] == KIND_OK
    assert row["state"] == "Configured"
    assert row["plan"] == "max"


def test_claude_oauth_token_with_no_account_details():
    """The shape a token in the env produces: logged in, nothing else known."""
    row = auth_health.parse_claude_auth_status(CLAUDE_OAUTH_TOKEN, 0)
    assert row["kind"] == KIND_OK
    assert row["method"] == "oauth_token"
    assert row["account"] == ""


def test_claude_api_key_is_a_warning():
    """This install is subscription-only; a visible API key is a real conflict."""
    row = auth_health.parse_claude_auth_status(CLAUDE_API_KEY, 0)
    assert row["kind"] == KIND_WARN
    assert "ANTHROPIC_API_KEY" in row["detail"]


def test_claude_logged_out():
    row = auth_health.parse_claude_auth_status(CLAUDE_LOGGED_OUT, 1)
    assert row["kind"] == KIND_DANGER
    assert row["state"] == "Not signed in"


def test_claude_null_json_fields_do_not_become_the_string_none():
    row = auth_health.parse_claude_auth_status(CLAUDE_API_KEY, 0)
    assert row["account"] == ""
    assert row["plan"] == ""


def test_claude_tolerates_a_banner_before_the_json():
    row = auth_health.parse_claude_auth_status("Checking...\n" + CLAUDE_SUBSCRIPTION, 0)
    assert row["kind"] == KIND_OK


def test_claude_parser_is_total():
    for raw in ("", "not json", "{", "{}", "[]", "null"):
        row = auth_health.parse_claude_auth_status(raw, 1)
        assert row["kind"] in (KIND_OK, KIND_WARN, KIND_DANGER, KIND_STOPPED)


def test_claude_probe_never_claims_verified():
    """The load-bearing distinction of this module.

    `claude auth status` answered `loggedIn: true` for a deliberately invalid
    token, so no result from this probe may be presented as proof the credential
    works. If someone ever makes this check hit the API, this test should be
    changed deliberately — not deleted because it started failing.
    """
    row = asyncio.run(auth_health.check_claude())
    assert row["verified"] is False
    assert row["unverified_because"]


def test_github_probe_claims_verified():
    """`gh auth status` does reach GitHub, so its pass IS evidence."""
    row = asyncio.run(auth_health.check_github())
    # Unavailable (no gh on PATH) drops the claim rather than asserting it.
    assert row["verified"] is (row["state"] != "Unavailable")


def test_summarize_counts_by_kind():
    rows = [{"kind": KIND_OK}, {"kind": KIND_WARN}, {"kind": KIND_OK}]
    assert auth_health.summarize(rows) == {
        KIND_OK: 2, KIND_WARN: 1, KIND_DANGER: 0, KIND_STOPPED: 0, "total": 3,
    }


def test_check_all_sorts_worst_first_and_never_raises():
    rows = asyncio.run(auth_health.check_all())
    assert {r["name"] for r in rows} == {"claude", "github"}
    from services.mcp_health import SEVERITY
    sev = [SEVERITY.get(r["kind"], 2) for r in rows]
    assert sev == sorted(sev)
