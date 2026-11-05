"""Every boundary the file log writes reaches the transcript tree.

THE BUG THIS PINS, found 2026-10-02: `note_auto_compact` writes a boundary with
`mode="auto"`, the mirror copies the mode into `chat_message.boundary_mode`, and
`ck_chat_message_boundary_mode` admitted only `clear`/`compact`. Every
auto-compaction raised CheckViolation inside `chat_branches.record_message`,
which is best-effort and swallows it, so nothing failed visibly and the divider
simply never got a place in the tree.

Because the mirror swallows the error, "no exception" proves nothing here. Each
case drives the real writer through the real mirror and then reads the tree
back, so a refused row shows up as a missing node.
"""
import asyncio
import os
import uuid

import pytest

from services import chat_log, chat_store, ownership

pytestmark = pytest.mark.skipif(
    not os.environ.get("WORKSHOP_DATABASE_URL"),
    reason="no WORKSHOP_DATABASE_URL; see infra/postgres/docker-compose.yml",
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A throwaway org and file storage, with tenancy pinned to it, yielding a
    reader for one file thread's nodes in the tree."""
    from db.engine import owner_session, tenant_session
    from db.ids import uuid7
    from db.models import Membership, Organization, UserAccount
    from db import chat_repo
    from services import tenancy
    from tests.conftest import DB_ORG_PREFIX, _delete_test_orgs, _delete_test_users

    tag = uuid.uuid4().hex[:8]
    oid = None
    uid = None
    try:
        with owner_session() as s:
            o = Organization(id=uuid7(), slug=f"{DB_ORG_PREFIX}bm-{tag}",
                             name="Boundary modes")
            s.add(o); s.flush(); oid = o.id
            u = UserAccount(id=uuid7(), email=f"bm-{tag}@example.test")
            s.add(u); s.flush(); uid = u.id
        with tenant_session(oid) as s:
            s.add(Membership(org_id=oid, user_id=uid, role="owner"))

        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
        monkeypatch.setattr(tenancy, "available", lambda: True)
        monkeypatch.setattr(tenancy, "org_and_user", lambda: (oid, uid))
        ownership.set_current_owner(1)

        def nodes(tid: str) -> list[dict]:
            with tenant_session(oid) as s:
                t = chat_repo.thread_by_ref(s, tid)
                return chat_repo.transcript(s, t["id"]) if t else []

        yield nodes
    finally:
        ownership.clear_current_owner()
        _delete_test_orgs([oid])
        _delete_test_users([uid])


def _write(writer) -> str:
    async def go():
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "one")
        chat_store.append_message(tid, "assistant", "two")
        writer(tid)
        await chat_log.drain_mirror_writes()
        return tid
    return asyncio.run(go())


WRITERS = {
    "auto": chat_store.note_auto_compact,
    "reset": chat_store.note_session_reset,
    "clear": chat_store.clear_thread_context,
    "compact": lambda tid: chat_store.set_summary(tid, "a summary",
                                                  folded_through=1),
}


@pytest.mark.parametrize("mode", sorted(WRITERS))
def test_a_boundary_reaches_the_tree_with_its_mode(tree, mode):
    tid = _write(WRITERS[mode])
    got = [(n["kind"], n.get("mode")) for n in tree(tid)]
    assert got == [("message", None), ("message", None), ("boundary", mode)]


def test_every_mode_the_tree_admits_has_a_writer_here():
    """The constraint and this file's writers describe the same set, so a mode
    added to one without the other is a failure rather than a silent gap."""
    from db.models import BOUNDARY_MODES
    assert set(BOUNDARY_MODES) == set(WRITERS)
