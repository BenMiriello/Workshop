"""Taking a message back removes it rather than leaving a second line, and the
session point a reply recorded is what going back resumes at."""
import pytest

from tests.test_db_chat import _say, chat  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml")

CLI = {"cli": {"session": "s1", "at": "entry-2"}}


def _line(chat):
    from db.engine import tenant_session
    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "first", external_id="EV1")
        _say(s, t, "assistant", "answer", external_id="EV2", meta=CLI)
        _say(s, t, "user", "take me back", external_id="EV3")
        _say(s, t, "assistant", "half a reply", external_id="EV4")


def test_unsend_removes_the_message_and_its_reply_without_a_second_line(chat):
    from db import chat_repo
    from db.engine import tenant_session
    from services import chat_branches
    _line(chat)
    out = chat_branches._sync_unsend(chat["org"], chat["thread"], "EV3")
    assert out["branched"] is False
    assert out["rewind"] == {"session": "s1", "at": "entry-2"}
    with tenant_session(chat["org"]) as s:
        assert len(chat_repo.list_branches(s, chat["thread"])) == 1
        assert [n["content"] for n in chat_repo.transcript(s, chat["thread"])] == ["first", "answer"]
    state = chat_branches._sync_state(chat["org"], chat["thread"])
    assert state["hidden"] == {"EV3", "EV4"}


def test_unsend_leaves_rows_another_line_still_reaches(chat):
    from db import chat_repo
    from db.engine import tenant_session
    from services import chat_branches
    _line(chat)
    with tenant_session(chat["org"]) as s:
        nodes = chat_repo.transcript(s, chat["thread"])
        mine = next(n for n in nodes if n["content"] == "take me back")
        chat_repo.branch_from(s, chat["thread"], mine["id"])
        _say(s, chat["thread"], "user", "on line two", external_id="EV5")
    out = chat_branches._sync_unsend(chat["org"], chat["thread"], "EV3")
    assert out["branched"] is True
    with tenant_session(chat["org"]) as s:
        assert len(chat_repo.list_branches(s, chat["thread"])) == 3


def test_no_rewind_point_after_a_message_of_yours_or_a_reply_without_one(chat):
    from db.engine import tenant_session
    from services import chat_branches
    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        u = _say(s, t, "user", "first", external_id="EV1")
        a = _say(s, t, "assistant", "answer", external_id="EV2")
        _say(s, t, "user", "next", external_id="EV3")
        assert chat_branches._rewind_point(s, u["id"]) is None
        assert chat_branches._rewind_point(s, a["id"]) is None


def test_a_compact_after_the_point_still_leaves_a_rewind_point(chat):
    """Whether the CLI can find the point past a compaction is its file's
    answer, asked when the next turn starts, not the transcript's."""
    from db.engine import tenant_session
    from services import chat_branches
    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "first", external_id="EV1")
        a = _say(s, t, "assistant", "answer", external_id="EV2", meta=CLI)
        _say(s, t, "assistant", "", kind="boundary", boundary_mode="compact", external_id="EV3")
        _say(s, t, "user", "after the compact", external_id="EV4")
        assert chat_branches._rewind_point(s, a["id"]) == CLI["cli"]
