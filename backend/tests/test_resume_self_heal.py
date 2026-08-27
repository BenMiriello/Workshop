"""A failed --resume turn detaches the thread's session ONLY when the session
is actually the fault.

The old rule detached on any error, so a single 529, an expired token, or a
dropped socket threw away the durable CLI transcript and forced a lossy
summary+tail rebuild on the next turn. That is context leaving before a
compaction, which the chat-context model of record forbids. The classifier under
test keeps a good session on transient failures and detaches only on a genuine
resume miss (or a startup failure that produced nothing and carries no transient
signature, which preserves the original anti-loop guard).
"""
from services import build_streaming as bs


def test_resume_miss_signature_detaches():
    # The CLI's own words when the session is gone on disk.
    d = "Ended unexpectedly (exit 1): No conversation found with session ID abc123"
    assert bs._resume_failure_is_session_fault(d, produced_output=False) is True
    # Even if the turn somehow produced output, an explicit miss is a miss.
    assert bs._resume_failure_is_session_fault(d, produced_output=True) is True


def test_transient_failures_keep_the_session():
    for d in (
        "API Error: 529 Overloaded",
        "Rate limit exceeded, please try again later",
        "429 Too Many Requests",
        "OAuth access token has been revoked",
        "Not logged in. Please run /login",
        "socket hang up",
        "ECONNRESET: connection reset by peer",
        "You've hit your session limit, resets 9:40pm",
    ):
        assert bs._resume_failure_is_session_fault(d, produced_output=False) is False, d


def test_no_signature_no_output_detaches_but_with_output_keeps():
    # A resume that died at startup with nothing to show points at the session.
    assert bs._resume_failure_is_session_fault("", produced_output=False) is True
    assert bs._resume_failure_is_session_fault("Ended unexpectedly (exit 1).",
                                               produced_output=False) is True
    # A resume that loaded the session, did work, then failed later is not the
    # session's fault: keep it.
    assert bs._resume_failure_is_session_fault("some later crash",
                                               produced_output=True) is False


def test_auth_failure_keeps_the_session_even_with_no_output():
    # A credential problem kills the process before any output, but the session
    # on disk is fine, so it must not be discarded.
    d = "Ended unexpectedly (exit 1): authentication_error: invalid api key"
    assert bs._resume_failure_is_session_fault(d, produced_output=False) is False
