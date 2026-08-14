"""The chat transcript tree and its one operation (ADR 0020).

What these pin, in order of how much it would hurt to get wrong:

  nothing is ever destroyed by going back      the whole safety claim
  switching back restores the transcript       exactly, not approximately
  the interrupted reply renders in true order  the defect the tree exists to fix
  parent_id cannot be rewritten                what makes a cycle unbuildable
  a parent cannot live in another thread       what makes the path walk sound

Skipped when there is no database, like the other db tests.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)

pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DatabaseError, IntegrityError  # noqa: E402


@pytest.fixture
def chat():
    """A throwaway org, a user, and one empty thread."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Membership, Organization, UserAccount
    from db import chat_repo

    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs

    tag = uuid.uuid4().hex[:8]
    oid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}c-{tag}",
                             name="Chat test")
            s.add(o)
            s.flush()
            oid = o.id
            u = UserAccount(id=uuid7(), email=f"chat-{tag}@example.test")
            s.add(u)
            s.flush()
            uid = u.id

        with tenant_session(oid) as s:
            s.add(Membership(org_id=oid, user_id=uid, role="owner"))
            s.flush()
            t = chat_repo.create_thread(s, oid, uid, context_key="general",
                                        scope="workshop")
        yield {"org": oid, "user": uid, "thread": t["id"]}
    finally:
        _delete_test_orgs([oid])


def _say(s, thread, role, content, **kw):
    from db import chat_repo
    return chat_repo.append_message(s, thread, role=role, content=content, **kw)


def _texts(nodes):
    return [n["content"] for n in nodes]


# --- the shape ---------------------------------------------------------------

def test_a_linear_thread_renders_in_order(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        _say(s, chat["thread"], "user", "one")
        _say(s, chat["thread"], "assistant", "two")
        _say(s, chat["thread"], "user", "three")
        assert _texts(chat_repo.transcript(s, chat["thread"])) == \
            ["one", "two", "three"]


def test_seq_is_a_cursor_not_the_render_order(chat):
    """They were the same number in the file log, and that is precisely why an
    interrupting message could not be placed."""
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        a = _say(s, chat["thread"], "user", "one")
        b = _say(s, chat["thread"], "assistant", "two")
        assert b["seq"] == a["seq"] + 1
        nodes = chat_repo.transcript(s, chat["thread"])
        # Order comes from the parent chain, which is what `depth` reports.
        assert [n["depth"] for n in nodes] == [1, 0]


def test_an_interrupted_reply_renders_in_true_order(chat):
    """The defect the tree exists to remove.

    A reply that was interrupted is stored as two assistant messages with the
    interrupting message threaded BETWEEN them. No cut token, no index, no
    reconstruction: the order is what the rows say.
    """
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "do the thing")
        before = _say(s, t, "assistant", "starting on it")
        _say(s, t, "user", "actually, wait")
        _say(s, t, "assistant", "ok, stopping")

        assert _texts(chat_repo.transcript(s, t)) == [
            "do the thing", "starting on it", "actually, wait", "ok, stopping"]
        # The mid-reply message hangs off the first stretch, not off the turn.
        nodes = chat_repo.transcript(s, t)
        assert nodes[2]["parent_id"] == before["id"]


# --- going back --------------------------------------------------------------

def test_going_back_hides_the_tail_but_destroys_nothing(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "keep")
        anchor = _say(s, t, "assistant", "anchor")
        _say(s, t, "user", "drop me")
        _say(s, t, "assistant", "drop me too")
        assert len(chat_repo.transcript(s, t)) == 4

        chat_repo.branch_from(s, t, anchor["id"])
        assert _texts(chat_repo.transcript(s, t)) == ["keep", "anchor"]

        # Nothing was deleted. The rows are all still there.
        alive = s.scalar(text(
            "SELECT count(*) FROM chat_message "
            "WHERE thread_id = :t AND deleted_at IS NULL"), {"t": t})
        assert alive == 4


def test_the_abandoned_line_is_still_reachable(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "keep")
        anchor = _say(s, t, "assistant", "anchor")
        _say(s, t, "user", "the old way")

        chat_repo.branch_from(s, t, anchor["id"])
        _say(s, t, "user", "the new way")

        branches = chat_repo.list_branches(s, t)
        assert len(branches) == 2
        by_name = {b["name"]: b for b in branches}
        assert set(by_name) == {"Main", "Branch 2"}
        assert by_name["Branch 2"]["is_current"] is True

        # The original line renders untouched from its own ref.
        old = chat_repo.transcript(s, t, branch_id=by_name["Main"]["id"])
        assert _texts(old) == ["keep", "anchor", "the old way"]


def test_switching_back_restores_the_transcript_exactly(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "keep")
        anchor = _say(s, t, "assistant", "anchor")
        _say(s, t, "user", "the old way")
        before = chat_repo.transcript(s, t)
        main = chat_repo.current_branch(s, t)

        chat_repo.branch_from(s, t, anchor["id"])
        _say(s, t, "user", "the new way")
        assert _texts(chat_repo.transcript(s, t)) == ["keep", "anchor",
                                                      "the new way"]

        chat_repo.switch_branch(s, t, main["id"])
        after = chat_repo.transcript(s, t)
        assert [n["id"] for n in after] == [n["id"] for n in before]
        assert _texts(after) == ["keep", "anchor", "the old way"]


def test_a_new_message_lands_on_the_branch_not_the_old_tail(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        anchor = _say(s, t, "user", "anchor")
        _say(s, t, "assistant", "old tail")
        chat_repo.branch_from(s, t, anchor["id"])
        new = _say(s, t, "user", "new")

        nodes = chat_repo.transcript(s, t)
        assert _texts(nodes) == ["anchor", "new"]
        assert nodes[-1]["id"] == new["id"]
        assert nodes[-1]["parent_id"] == anchor["id"]


# --- the two no-ops a real operator will hit ---------------------------------

def test_branching_from_the_head_is_a_no_op(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "one")
        head = _say(s, t, "assistant", "two")
        before = chat_repo.current_branch(s, t)

        got = chat_repo.branch_from(s, t, head["id"])
        assert got["id"] == before["id"]
        assert len(chat_repo.list_branches(s, t)) == 1


def test_branching_twice_from_one_anchor_reuses_the_empty_branch(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        anchor = _say(s, t, "user", "anchor")
        _say(s, t, "assistant", "tail")

        first = chat_repo.branch_from(s, t, anchor["id"])
        second = chat_repo.branch_from(s, t, anchor["id"])
        assert first["id"] == second["id"]
        assert len(chat_repo.list_branches(s, t)) == 2


def test_branch_ordinals_do_not_repeat(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        a = _say(s, t, "user", "a")
        _say(s, t, "assistant", "b")
        chat_repo.branch_from(s, t, a["id"])
        _say(s, t, "user", "c")
        chat_repo.branch_from(s, t, a["id"])
        _say(s, t, "user", "d")
        chat_repo.branch_from(s, t, a["id"])

        ordinals = sorted(b["ordinal"] for b in chat_repo.list_branches(s, t))
        assert ordinals == [1, 2, 3, 4]


def test_the_picker_puts_the_most_recently_active_first(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        a = _say(s, t, "user", "a")
        _say(s, t, "assistant", "on main")
        chat_repo.branch_from(s, t, a["id"])
        _say(s, t, "user", "on the branch")

        names = [b["name"] for b in chat_repo.list_branches(s, t)]
        assert names[0] == "Branch 2"


# --- the invariants ----------------------------------------------------------

def test_parent_id_is_immutable(chat):
    """What makes a cycle unbuildable: there is no UPDATE that could make one."""
    from db.engine import tenant_session

    with tenant_session(chat["org"]) as s:
        a = _say(s, chat["thread"], "user", "a")
        b = _say(s, chat["thread"], "assistant", "b")
        with pytest.raises(DatabaseError):
            s.execute(text("UPDATE chat_message SET parent_id = :p WHERE id = :m"),
                      {"p": b["id"], "m": a["id"]})


def test_a_parent_may_not_live_in_another_thread(chat):
    """Composite (id, thread_id) FK. Without it the path walk silently crosses
    conversations and no tenant check would notice."""
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        other = chat_repo.create_thread(s, chat["org"], chat["user"])
        foreign = _say(s, other["id"], "user", "elsewhere")
        with pytest.raises(IntegrityError):
            chat_repo.append_message(s, chat["thread"], role="user",
                                     content="x", parent_id=foreign["id"])


def test_a_resent_client_id_appends_nothing(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        cid = uuid.uuid4().hex
        first = chat_repo.append_message(s, t, role="user", content="once",
                                         client_id=cid)
        again = chat_repo.append_message(s, t, role="user", content="once",
                                         client_id=cid)
        assert again["duplicate"] is True
        assert again["id"] == first["id"]
        assert len(chat_repo.transcript(s, t)) == 1


def test_a_branch_cannot_be_switched_to_from_another_thread(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        other = chat_repo.create_thread(s, chat["org"], chat["user"])
        foreign_branch = other["current_branch_id"]
        with pytest.raises(chat_repo.BranchError):
            chat_repo.switch_branch(s, chat["thread"], foreign_branch)


def test_branching_from_a_foreign_message_is_refused(chat):
    from db.engine import tenant_session
    from db import chat_repo

    with tenant_session(chat["org"]) as s:
        other = chat_repo.create_thread(s, chat["org"], chat["user"])
        foreign = _say(s, other["id"], "user", "elsewhere")
        with pytest.raises(chat_repo.MessageNotFound):
            chat_repo.branch_from(s, chat["thread"], foreign["id"])


# --- the glue the sync endpoint actually uses --------------------------------

def test_the_sync_filter_is_inert_until_a_thread_is_branched(chat):
    """The property that makes this safe to ship: a conversation nobody has
    branched is served exactly as it was, with no flag involved."""
    from db.engine import tenant_session
    from services import chat_branches

    with tenant_session(chat["org"]) as s:
        _say(s, chat["thread"], "user", "one", external_id="EV1")
        _say(s, chat["thread"], "assistant", "two", external_id="EV2")

    state = chat_branches._sync_state(chat["org"], chat["thread"])
    assert state["count"] == 1
    assert state["hidden"] is None
    events = [{"id": "EV1", "kind": "message"}, {"id": "EV2", "kind": "message"}]
    assert chat_branches.keep_visible(events, state["hidden"]) == events


def test_after_a_fork_the_sync_filter_drops_the_abandoned_tail(chat):
    from db.engine import tenant_session
    from db import chat_repo
    from services import chat_branches

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "keep", external_id="EV1")
        anchor = _say(s, t, "assistant", "anchor", external_id="EV2")
        _say(s, t, "user", "abandoned", external_id="EV3")
        chat_repo.branch_from(s, t, anchor["id"])

    state = chat_branches._sync_state(chat["org"], chat["thread"])
    assert state["count"] == 2
    assert state["hidden"] == {"EV3"}

    events = [{"id": "EV1", "kind": "message"}, {"id": "EV2", "kind": "message"},
              {"id": "EV3", "kind": "message"}]
    kept = [e["id"] for e in chat_branches.keep_visible(events, state["hidden"])]
    assert kept == ["EV1", "EV2"]


def test_an_event_with_no_row_is_kept(chat):
    """The mirror-write is best-effort, so an event the tree has not heard of
    must still render. Dropping it would make a message the operator just sent
    disappear, which is the one failure that is not recoverable by looking
    again."""
    from db.engine import tenant_session
    from db import chat_repo
    from services import chat_branches

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        a = _say(s, t, "user", "keep", external_id="EV1")
        _say(s, t, "assistant", "tail", external_id="EV2")
        chat_repo.branch_from(s, t, a["id"])

    state = chat_branches._sync_state(chat["org"], chat["thread"])
    events = [{"id": "EV1", "kind": "message"}, {"id": "EV2", "kind": "message"},
              {"id": "EV-UNMIRRORED", "kind": "message"},
              {"id": "EV-RUN", "kind": "run"}]
    kept = [e["id"] for e in chat_branches.keep_visible(events, state["hidden"])]
    assert kept == ["EV1", "EV-UNMIRRORED", "EV-RUN"]


def test_an_anchor_resolves_from_a_file_event_id(chat):
    """The client points at what it is rendering, which is the log's ULID."""
    from db.engine import tenant_session
    from services import chat_branches

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "one", external_id="EVA")
        _say(s, t, "assistant", "two", external_id="EVB")
        _say(s, t, "user", "three", external_id="EVC")

    out = chat_branches._sync_fork(chat["org"], chat["thread"], "EVB")
    assert out["branch"]["name"] == "Branch 2"
    with tenant_session(chat["org"]) as s:
        assert _texts(chat_repo_transcript(s, chat["thread"])) == ["one", "two"]


def test_an_interrupted_reply_anchors_on_its_last_stretch(chat):
    """One file event became several rows. "Branch from this reply" means from
    the END of it; anchoring on the first stretch would drop the rest of the
    very reply being pointed at."""
    from db.engine import tenant_session
    from services import chat_branches

    with tenant_session(chat["org"]) as s:
        t = chat["thread"]
        _say(s, t, "user", "go", external_id="EV1")
        _say(s, t, "assistant", "first stretch", external_id="EV2")
        _say(s, t, "user", "wait", external_id="EV3")
        _say(s, t, "assistant", "second stretch", external_id="EV2")
        _say(s, t, "assistant", "later reply", external_id="EV4")

    chat_branches._sync_fork(chat["org"], chat["thread"], "EV2")
    with tenant_session(chat["org"]) as s:
        assert _texts(chat_repo_transcript(s, chat["thread"])) == [
            "go", "first stretch", "wait", "second stretch"]


def chat_repo_transcript(s, thread_id):
    from db import chat_repo
    return chat_repo.transcript(s, thread_id)


def test_a_thread_resolves_by_new_uuid_and_by_legacy_hex(chat):
    from db.engine import tenant_session
    from db import chat_repo

    legacy = uuid.uuid4().hex
    with tenant_session(chat["org"]) as s:
        made = chat_repo.create_thread(s, chat["org"], chat["user"],
                                       external_id=legacy)
        assert chat_repo.thread_by_ref(s, made["id"])["id"] == made["id"]
        assert chat_repo.thread_by_ref(s, legacy)["id"] == made["id"]
        assert chat_repo.thread_by_ref(s, uuid.uuid4().hex) is None
