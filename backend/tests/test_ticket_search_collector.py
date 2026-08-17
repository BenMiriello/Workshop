"""Tickets join the semantic-search corpus (docs/platform-redesign/memory-and-
documentation-model-2026-08-14.md sec 8: "kinds: [ticket] is how documentation
and tickets integrate without merging the stores"). Before this, "have we
decided X" or "is there already a ticket for Y" found the discussion and the
design doc and never the work, because `_COLLECTORS` covered note/doc/chat/
vault and nothing enumerated tickets.

Covers:
  - `collect_tickets` enumerates every scope `routes.tasks_all.scope_roots()`
    returns and emits `kind="ticket"` chunks with a real scope_tree scope, the
    bare ticket id as `ref`, and title+body as the text;
  - EVERY key `scope_roots()` returns is already a real `scope_tree` scope, so
    no translation happens here at all. It used to also return `build:<slug>`
    for an unlinked build agent, which had to be translated on every rebuild;
    that key is deleted;
  - the emitted ref actually resolves through `services.refs.resolve_ref` to a
    live href (Phase 3's own acceptance test: a hit you cannot open is a bug);
  - a ticket chunk is scope-filtered by `semantic_index.search` exactly like
    every other kind;
  - `memory_policy.resolve("ticket", ...)` falls through to "inherit" (no
    `ticket` key in DEFAULT_DEFAULTS), which is the CORRECT behaviour: tickets
    follow the tree, and adding a default would change that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import tasks_all  # noqa: E402
from services import knowledge, memory_policy, refs, semantic_index as si, tasks  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    ws = tmp_path / "workshop"
    proj = tmp_path / "birds"
    dev = tmp_path / "dev-scope"
    ws.mkdir()
    proj.mkdir()
    dev.mkdir()
    return {"workshop": ws, "project:birds": proj, "development": dev}


@pytest.fixture
def patched_scope_roots(roots, monkeypatch):
    monkeypatch.setattr(tasks_all, "scope_roots", lambda: list(roots.items()),
                        raising=False)
    return roots


class TestCollectTickets:
    def test_emits_ticket_chunks_with_real_scope_and_bare_id_ref(self, patched_scope_roots):
        roots = patched_scope_roots
        t = tasks.add(roots["workshop"], title="File the birds ticket",
                      body="Longer spec text.")
        chunks = si.collect_tickets()
        mine = [c for c in chunks if c.ref == t["id"]]
        assert len(mine) == 1
        c = mine[0]
        assert c.kind == "ticket"
        assert c.scope == "workshop"
        assert c.title == "File the birds ticket"
        assert "File the birds ticket" in c.text
        assert "Longer spec text." in c.text

    def test_covers_every_scope_root_not_just_one(self, patched_scope_roots):
        roots = patched_scope_roots
        a = tasks.add(roots["workshop"], title="Workshop ticket")
        b = tasks.add(roots["project:birds"], title="Birds ticket")
        c = tasks.add(roots["development"], title="Dev ticket")
        refs_seen = {ch.ref: ch.scope for ch in si.collect_tickets()}
        assert refs_seen[a["id"]] == "workshop"
        assert refs_seen[b["id"]] == "project:birds"
        assert refs_seen[c["id"]] == "development"

    def test_every_scope_it_enumerates_is_already_a_real_scope_key(self):
        """There is no translation step left in `collect_tickets`, and this is
        what makes removing it safe: `scope_roots()` emits only keys
        `scope_tree.parse` accepts. The one exception was `build:<slug>` for an
        unlinked build agent, and that key is deleted."""
        from services import scope_tree

        for scope, _root in tasks_all.scope_roots():
            scope_tree.parse(scope)  # raises if it is not a real scope key

    def test_a_scope_with_no_store_yet_is_skipped_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks_all, "scope_roots",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                            raising=False)
        assert si.collect_tickets() == []


class TestTheRefResolves:
    def test_a_ticket_hit_ref_round_trips_through_resolve_ref(self, patched_scope_roots):
        roots = patched_scope_roots
        t = tasks.add(roots["workshop"], title="Resolvable ticket")
        ref_str = refs.ticket_ref(t["id"])
        resolved = refs.resolve_ref(ref_str)
        assert resolved is not None
        assert resolved["kind"] == "ticket"
        assert resolved["title"] == "Resolvable ticket"
        assert resolved["href"] == f"/tickets/workshop/{t['id']}"
        assert resolved["status"] == "todo"

    def test_item_ref_produces_the_same_ref_the_resolver_expects(self):
        assert si._item_ref("ticket", "abc123", "workshop") == "workshop:ticket/abc123"


class TestScopeFilteringAppliesToTickets:
    @pytest.fixture(autouse=True)
    def _tmp_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "index_dir", lambda: tmp_path)
        monkeypatch.setattr(si, "_resolve_chunk_scope", lambda kind, raw: raw)

    def test_a_ticket_is_included_or_excluded_by_scope_like_any_chunk(self, monkeypatch):
        meta = [
            {"kind": "ticket", "ref": "t1", "title": "Workshop work", "scope": "workshop",
             "ts": "", "text": "workshop scoped ticket", "digest": "1"},
            {"kind": "ticket", "ref": "t2", "title": "Dev work", "scope": "development",
             "ts": "", "text": "development scoped ticket", "digest": "2"},
        ]
        si._save(meta, np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
        monkeypatch.setattr(si, "embed", lambda t: np.array([[1.0, 0.0]], dtype=np.float32))

        workshop_hits = si.search("q", kinds=("ticket",), scope={"workshop"}, min_score=0.5)["hits"]
        assert {h["ref"] for h in workshop_hits} == {"t1"}

        dev_hits = si.search("q", kinds=("ticket",), scope={"development"}, min_score=0.5)["hits"]
        assert {h["ref"] for h in dev_hits} == {"t2"}


class TestTicketMemoryPolicyDefault:
    def test_ticket_share_falls_through_to_inherit(self, tmp_path, monkeypatch):
        """No `ticket` key in DEFAULT_DEFAULTS, and none should be added: a
        ticket follows the tree exactly like the plain `reach()` rule, which is
        what `resolve` falling through to "inherit" already gives it."""
        monkeypatch.setattr(memory_policy, "STORE", tmp_path / "memory-policy.json")
        assert memory_policy.resolve("ticket", "project:birds") == "inherit"


class TestKindsVocabulary:
    def test_ticket_is_a_real_kind_and_a_default_one(self):
        assert "ticket" in si.KINDS
        assert "ticket" in si.DEFAULT_KINDS
        assert si._COLLECTORS["ticket"] is si.collect_tickets
