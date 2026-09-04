"""One address and one parser for the secret env file.

The bug this locks down was silent by construction: one file had two variable
names and two default directories, four hand-rolled parsers that stripped quotes
differently, and a symlink joining the two locations so nothing ever failed. A
missing key reads as "not configured" to every consumer, so half the system
reading the wrong path would have looked like a feature being off.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import auth_env


def test_parse_plain_pairs():
    assert auth_env.parse("A=1\nB=2\n") == {"A": "1", "B": "2"}


def test_parse_skips_comments_and_blanks():
    assert auth_env.parse("# note\n\n  \nA=1\n") == {"A": "1"}


def test_parse_handles_export_prefix():
    """Three of the readers of this file are shell scripts that source it."""
    assert auth_env.parse("export TOKEN=abc\n") == {"TOKEN": "abc"}


@pytest.mark.parametrize("line,expected", [
    ('T="abc"', "abc"),
    ("T='abc'", "abc"),
    ("T=abc", "abc"),
    ('T="ab c"', "ab c"),
])
def test_parse_strips_one_matched_quote_pair(line, expected):
    assert auth_env.parse(line + "\n")["T"] == expected


def test_parse_keeps_unmatched_quotes():
    """Stripping quote characters blindly is what made two readers disagree."""
    assert auth_env.parse('T="abc\n')["T"] == '"abc'


def test_parse_keeps_equals_inside_a_value():
    """Base64 and JWT-ish secrets end in `=` padding."""
    assert auth_env.parse("T=abc==\n")["T"] == "abc=="


def test_parse_is_total():
    for raw in ("", "\n", "no equals sign", "=novalue", "   =  "):
        assert isinstance(auth_env.parse(raw), dict)


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert auth_env.load_path(tmp_path / "nope.env") == {}


def test_a_directory_is_not_an_error(tmp_path):
    assert auth_env.load_path(tmp_path) == {}


def test_load_path_reads_the_file(tmp_path):
    p = tmp_path / "auth.env"
    p.write_text("CLAUDE_CODE_OAUTH_TOKEN=tok\n")
    assert auth_env.load_path(p)["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_env_var_names_the_file(tmp_path, monkeypatch):
    target = tmp_path / "custom.env"
    monkeypatch.setenv(auth_env.ENV_VAR, str(target))
    assert auth_env.auth_env_path() == target


def test_legacy_name_still_works(tmp_path, monkeypatch):
    """An install may set the old name in a systemd unit this commit can't reach."""
    target = tmp_path / "legacy.env"
    monkeypatch.delenv(auth_env.ENV_VAR, raising=False)
    monkeypatch.setenv(auth_env.LEGACY_ENV_VAR, str(target))
    assert auth_env.auth_env_path() == target


def test_the_current_name_wins_over_the_legacy_one(tmp_path, monkeypatch):
    monkeypatch.setenv(auth_env.ENV_VAR, str(tmp_path / "new.env"))
    monkeypatch.setenv(auth_env.LEGACY_ENV_VAR, str(tmp_path / "old.env"))
    assert auth_env.auth_env_path().name == "new.env"


def test_default_sits_under_the_config_dir(tmp_path, monkeypatch):
    """No hardcoded home directory anywhere in the resolution."""
    monkeypatch.delenv(auth_env.ENV_VAR, raising=False)
    monkeypatch.delenv(auth_env.LEGACY_ENV_VAR, raising=False)
    monkeypatch.setenv("WORKSHOP_CONFIG_DIR", str(tmp_path))
    assert auth_env.auth_env_path() == tmp_path / "auth.env"


def test_value_prefers_the_process_environment(tmp_path, monkeypatch):
    p = tmp_path / "auth.env"
    p.write_text("K=from_file\n")
    monkeypatch.setenv("K", "from_env")
    assert auth_env.value("K", path=p) == "from_env"


def test_value_falls_back_to_the_file(tmp_path, monkeypatch):
    p = tmp_path / "auth.env"
    p.write_text("K=from_file\n")
    monkeypatch.delenv("K", raising=False)
    assert auth_env.value("K", path=p) == "from_file"


def test_value_default_when_nowhere(tmp_path, monkeypatch):
    monkeypatch.delenv("K", raising=False)
    assert auth_env.value("K", "fallback", path=tmp_path / "none.env") == "fallback"


def test_value_is_read_at_call_time(tmp_path, monkeypatch):
    """Load-bearing: an import-time constant that missed the key once stayed
    empty for the life of the process, and realtime went dark for a whole
    commute from a file that was on disk the entire time."""
    p = tmp_path / "auth.env"
    p.write_text("K=first\n")
    monkeypatch.delenv("K", raising=False)
    assert auth_env.value("K", path=p) == "first"
    p.write_text("K=second\n")
    assert auth_env.value("K", path=p) == "second"


def test_apply_to_lets_the_file_win(tmp_path, monkeypatch):
    """Opposite of value(), deliberately: when handing a child its credentials,
    a blank or stale value in the parent must not shadow the real one on disk."""
    p = tmp_path / "auth.env"
    p.write_text("K=from_file\n")
    monkeypatch.setenv(auth_env.ENV_VAR, str(p))
    env = {"K": "", "OTHER": "kept"}
    auth_env.apply_to(env)
    assert env["K"] == "from_file"
    assert env["OTHER"] == "kept"


def test_claude_env_strips_the_api_key_even_if_the_file_sets_one(tmp_path, monkeypatch):
    """This install is subscription-only. The strip used to run BEFORE the file
    was sourced, so a key in the auth file would have been put straight back and
    every spawned turn would have billed against it."""
    p = tmp_path / "auth.env"
    p.write_text("ANTHROPIC_API_KEY=sk-should-not-survive\nCLAUDE_CODE_OAUTH_TOKEN=tok\n")
    monkeypatch.setenv(auth_env.ENV_VAR, str(p))
    from services import llm_router
    env = llm_router._claude_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_llm_router_and_the_loader_agree_on_the_path(tmp_path, monkeypatch):
    """The two addresses for one file are now one address."""
    monkeypatch.setenv(auth_env.ENV_VAR, str(tmp_path / "x.env"))
    from services import llm_router
    assert Path(llm_router.auth_env_path()) == auth_env.auth_env_path()


# THREE TESTS LEFT HERE ON 2026-09-04. They asserted that this file is read at
# CALL time rather than at import, using one app's poller and reason modules as
# the example. The rule is the platform's and is covered above over platform
# readers; the app-specific example moved to that app's own tests, which is
# where the obligation sits.
