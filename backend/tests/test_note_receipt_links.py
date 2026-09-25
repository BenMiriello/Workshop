"""A receipt names WHAT was written, and can be opened.

`apply_note_block` already knew the scope and the note id it had just created,
and threw both away into the sentence "Saved a note to workshop.". So the chat
could tell the operator a note existed and offer no way to go read it, on a
system where notes are a real, browsable surface (/memory over the scoped
knowledge store). It now returns those two alongside the label, and
build_streaming turns them into a link on the receipt.

The failure cases matter as much as the success one: a refusal or a parse error
produced no note, so it must produce no ref, or the transcript would offer a
link to something that was never written.
"""
from __future__ import annotations

import asyncio

import pytest

from services import task_scope
from wsplatform import paths


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    paths.operator_workspace("knowledge").mkdir(parents=True)
    # The fence's explicit `scope` is an operator-only override (see
    # apply_note_block): for anyone else it is a way to write into a scope the
    # chat is not bound to, `workshop` above all, which is read into every other
    # chat's ambient context. These cases exercise that override, so they need a
    # real operator pinned rather than the unset owner a bare unit test has.
    from services import auth_store, ownership
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    auth_store.init_db()
    op = auth_store.create_user("operator@example.com", "password1")
    auth_store.set_operator(op["id"])
    ownership.set_current_owner(op["id"])
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


def _apply(reply: str):
    return asyncio.run(task_scope.apply_note_block("workshop", reply))


def _block(payload: str) -> str:
    return f"Here you go.\n\n```note\n{payload}\n```\n"


def test_a_written_note_comes_back_with_somewhere_to_open_it(root):
    cleaned, labels, refs = _apply(_block(
        '{"title": "Ports are squatted", "body": "8012 fakes healthy.", "scope": "workshop"}'
    ))
    assert "```note" not in cleaned
    assert labels == ["Saved a note to workshop."]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["scope"] == "workshop"
    assert ref["title"] == "Ports are squatted"
    # The id is the store's slug for that title, and it is what /memory?note=
    # selects, so it has to be the id the file was actually written under.
    assert ref["id"].startswith("ports-are-squatted-")
    written = paths.operator_workspace("knowledge") / f"{ref['id']}.md"
    assert written.exists(), "the ref must point at a note that exists"
    assert "8012 fakes healthy." in written.read_text()


def test_no_block_writes_nothing_and_refs_nothing(root):
    cleaned, labels, refs = _apply("Just a reply, no block.")
    assert cleaned == "Just a reply, no block."
    assert labels == [] and refs == []


def test_an_unparseable_block_warns_without_a_link(root):
    _, labels, refs = _apply(_block("{not json"))
    assert labels and labels[0].startswith("(")
    # A warning never carries a ref: nothing was written to link to.
    assert refs == []


def test_a_rejected_scope_warns_without_a_link(root):
    _, labels, refs = _apply(_block(
        '{"title": "x", "body": "y", "scope": "not a real scope!!"}'
    ))
    assert labels and labels[0].startswith("(note skipped:")
    assert refs == []


def test_the_receipt_builder_links_a_note_and_never_links_a_warning():
    """The real build_receipts, not a copy: refs pair positionally with labels,
    and a parenthesised label is a warning that takes no ref even when one is
    offered."""
    from services.build_streaming import build_receipts

    note = build_receipts("note", ["Saved a note to workshop."],
                          [{"scope": "workshop", "id": "n1"}])
    assert note == [{
        "kind": "note", "label": "Saved a note to workshop.",
        "ref": {"scope": "workshop", "id": "n1"},
    }]

    # A refusal is a warning whatever kind produced it, and it drops the ref.
    refused = build_receipts("ticket", ["(a triager agent may not file tickets)"],
                             [{"scope": "x", "id": "y"}])
    assert refused[0]["kind"] == "warn" and "ref" not in refused[0]

    # No refs at all is the ordinary case for the other writers.
    assert build_receipts("learning", ["Captured a preference."]) == [
        {"kind": "learning", "label": "Captured a preference."},
    ]

    # Blank labels are dropped rather than becoming empty rows.
    assert build_receipts("note", ["  ", "Saved a note to workshop."]) == [
        {"kind": "note", "label": "Saved a note to workshop."},
    ]

    # A ref list that does not describe the labels one for one is dropped
    # ENTIRELY rather than paired by index. Attaching a ref to the wrong label
    # would link a receipt to a note it did not write, which is worse than
    # linking nowhere.
    mixed = build_receipts("note", ["  ", "Saved a note to workshop."],
                           [{"scope": "workshop", "id": "first"}])
    assert len(mixed) == 1 and "ref" not in mixed[0]
