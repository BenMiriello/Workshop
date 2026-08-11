"""A failed turn must say what the CLI said.

The bug being locked down: the child's stderr was captured to `stream.err` on
every turn and never read by anything, so a turn that died of an expired
credential reached the operator as "Ended unexpectedly (exit 1)" while the
sentence explaining it sat on disk. These tests assert the sentence gets out.
"""
from __future__ import annotations

import pytest

from services import build_streaming as bs


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Redirect the turn journal at a temp dir and hand back a writer."""
    monkeypatch.setattr(bs, "TURNS_DIR", tmp_path)

    def write(turn_id: str, content: str) -> str:
        d = tmp_path / turn_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "stream.err").write_text(content, encoding="utf-8")
        return turn_id

    return write


def test_missing_journal_is_empty_not_an_error(journal):
    assert bs._stderr_tail("no-such-turn") == ""


def test_empty_stderr_is_empty(journal):
    journal("t1", "")
    assert bs._stderr_tail("t1") == ""


def test_the_message_comes_back(journal):
    journal("t2", "Invalid API key · Please run /login\n")
    assert "Invalid API key" in bs._stderr_tail("t2")


def test_node_runtime_noise_is_dropped(journal):
    journal("t3", "(node:12345) ExperimentalWarning: stuff\n"
                  "(Use `node --trace-warnings ...` to show where)\n"
                  "Error: authentication failed\n")
    out = bs._stderr_tail("t3")
    assert "ExperimentalWarning" not in out
    assert "authentication failed" in out


def test_noise_only_stderr_yields_nothing_to_show(journal):
    journal("t4", "(node:1) Warning: a\nDeprecationWarning: b\n")
    assert bs._stderr_tail("t4") == ""


def test_a_word_containing_warning_is_not_treated_as_noise(journal):
    """Noise matches at the start of a line, so a real message survives."""
    journal("t5", "Refusing to continue: warnings were requested but auth failed\n")
    assert bs._stderr_tail("t5")


def test_long_stderr_keeps_the_end(journal):
    """The failure is written after whatever preceded it, so the tail is the part
    worth showing."""
    journal("t6", ("noise line that is quite long and repeated\n" * 500)
            + "FINAL: the actual reason it died\n")
    out = bs._stderr_tail("t6")
    assert "FINAL: the actual reason it died" in out
    assert len(out) <= bs._STDERR_TAIL_CHARS + 1  # +1 for the ellipsis marker


def test_huge_file_is_not_read_whole(journal, tmp_path):
    """A turn that spewed megabytes must not be loaded into memory to render a line."""
    journal("t7", "x" * 5_000_000 + "\nTAIL MARKER\n")
    out = bs._stderr_tail("t7")
    assert "TAIL MARKER" in out
    assert len(out) <= bs._STDERR_TAIL_CHARS + 1


def test_undecodable_bytes_do_not_raise(journal, tmp_path):
    d = tmp_path / "t8"
    d.mkdir(parents=True, exist_ok=True)
    (d / "stream.err").write_bytes(b"\xff\xfe bad bytes then: auth failed\n")
    assert "auth failed" in bs._stderr_tail("t8")


@pytest.mark.parametrize("text", [
    "Invalid API key · Please run /login",
    "OAuth token has expired",
    "401 Unauthorized",
    "authentication_error: invalid bearer token",
    "You are not logged in",
])
def test_credential_failures_are_recognised(text):
    assert bs._looks_like_auth_failure(text)


@pytest.mark.parametrize("text", [
    "ENOSPC: no space left on device",
    "Killed",
    "TypeError: undefined is not a function",
    "",
])
def test_ordinary_crashes_are_not_called_credential_problems(text):
    assert not bs._looks_like_auth_failure(text)


def test_our_own_error_wins_over_the_childs():
    """When THIS module is what failed, it knows more than the child's stderr.

    `turn.error` is only set for failures we detected ourselves (journal open,
    input channel, missing binary), so it is the better message in exactly those
    cases and the child's output is the better one everywhere else.
    """
    out = bs._failure_text(1, "claude CLI not found", "some child noise about a socket")
    assert "claude CLI not found" in out
    assert "socket" not in out


def test_stderr_is_used_when_we_have_no_error_of_our_own():
    out = bs._failure_text(1, "", "Invalid API key · Please run /login")
    assert "Invalid API key" in out


def test_the_old_bare_message_only_appears_when_there_is_nothing_to_say():
    """This exact string was the whole bug. It is now the last resort, not the
    first."""
    assert bs._failure_text(1, "", "") == "Ended unexpectedly (exit 1)."


def test_a_credential_failure_points_at_where_to_fix_it():
    out = bs._failure_text(1, "", "OAuth token has expired")
    assert "OAuth token has expired" in out
    assert "Settings" in out


def test_an_ordinary_crash_gets_no_credentials_advice():
    out = bs._failure_text(137, "", "Killed")
    assert "Killed" in out
    assert "credentials" not in out.lower()


@pytest.mark.parametrize("rc", [1, 2, 127, -1])
def test_the_exit_code_is_still_reported(rc):
    """It was never the problem that the code was shown, only that it was all
    that was shown."""
    assert f"exit {rc}" in bs._failure_text(rc, "", "something broke")


def test_whitespace_only_detail_is_treated_as_nothing():
    assert bs._failure_text(1, "   ", "\n\t ") == "Ended unexpectedly (exit 1)."
