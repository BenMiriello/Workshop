"""Chat with an agent exists (T2).

THE BUG THIS PINS. On 2026-08-16 there was NO way to chat with an agent at all.
`chat_store._promotion_patch` rewrote any `agent:<slug>` or `build:<slug>`
context_key to `project:<slug>` or `general` at creation AND on every subsequent
`_patch`, capturing the agent in a `participants` list. The model it migrated
into was never built: nothing in routes/chat.py, chat_context.py, mentions.py,
build_streaming.py or the voice path read `participants`. So:

  - `AssistantAgentContextProvider` and `BuildAgentContextProvider` were both
    unreachable, written in July and never once constructed from a chat;
  - 0 of 94 live threads had an `agent:` context key;
  - AgentDetail's "Start chat with agent" button opened a chat answered by the
    general assistant, with the agent's name in the title and its slug in a
    field nothing read.

`agent:<slug>` is now a durable context key answered by that agent. `build:` is
DELETED as a context kind, not folded to it: the fold was the second spelling
kept alive, and the stored threads were migrated
(`scripts/migrate_build_context_keys.py`). `participants` is deleted.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import chat_context, chat_store, ownership

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A private chat store. `ownership._RUNTIME` is redirected so a test that
    creates threads cannot write into the operator's real ones."""
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    chat_store._SUMMARY_CACHE.clear()
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()
        chat_store._SUMMARY_CACHE.clear()


# The two agent shapes, as `agents_registry.get` returns them. Stubbed rather
# than read off this instance, so the assertions are about the ROUTING and not
# about which agents happen to be installed here.
ASSISTANT_REC = {
    "slug": "quill",
    "name": "Quill",
    "runtime": "assistant",
    "role": None,
    "purpose": "Writes things down.",
    "definition": "Answer in one paragraph and stop.",
    "retired": False,
}

MAKER_REC = {
    "slug": "smith",
    "name": "Smith",
    # A `runtime` field is left here DELIBERATELY, and it is junk: the axis is
    # deleted, `_record_from_def` no longer emits the key, and nothing may read
    # it. It stays so a test keying on the wrong field still passes here and
    # fails in TestRoleNotRuntime below.
    "runtime": "builder",
    "role": "maker",
    "purpose": "Builds things.",
    "retired": False,
}


@pytest.fixture()
def registry(monkeypatch, tmp_path):
    """`agents_registry.get` and `build_agents.get_agent` over the two records."""
    from services import agents_registry, build_agents

    workdir = tmp_path / "smith"
    workdir.mkdir()
    (workdir / "AGENTS.md").write_text("Commit on green.\n")
    (workdir / "PROJECT.md").write_text("Build the thing.\n")

    by_slug = {"quill": ASSISTANT_REC, "smith": MAKER_REC}
    monkeypatch.setattr(agents_registry, "get", lambda s: by_slug.get(s))
    monkeypatch.setattr(agents_registry, "list_agents",
                        lambda **kw: [ASSISTANT_REC, MAKER_REC])

    def _get_agent(slug):
        if slug != "smith":
            raise KeyError(slug)
        return {"slug": "smith", "name": "Smith", "cwd": str(workdir),
                "purpose": "Builds things.", "project_slug": None}

    monkeypatch.setattr(build_agents, "get_agent", _get_agent)
    return workdir


# --- the key survives ----------------------------------------------------------
class TestTheKeySurvives:
    def test_creation_keeps_an_agent_key(self, store, registry):
        t = chat_store.create_thread("agent:quill", title="Chat with Quill")
        assert t["context_key"] == "agent:quill", (
            "_promotion_patch rewrote this at creation; that is the bug"
        )

    def test_a_later_patch_keeps_it(self, store, registry):
        """The half that made the bug unfixable from the outside. Even a thread
        that somehow got an `agent:` key lost it on the NEXT write of any field,
        because `_promotion_patch` ran inside `_patch`."""
        tid = chat_store.create_thread("agent:quill")["id"]
        chat_store.set_thread_mode(tid, "orchestrated")
        chat_store.set_thread_model(tid, "claude-haiku-4-5-20251001")
        chat_store.append_message(tid, "user", "hello")
        chat_store.archive_thread(tid)
        chat_store.restore_thread(tid)
        assert chat_store.get_thread(tid)["context_key"] == "agent:quill"

    def test_the_scope_is_the_agent(self, store, registry):
        t = chat_store.create_thread("agent:quill")
        assert t["scope"] == "agent:quill"

    def test_the_summary_row_keeps_it_too(self, store, registry):
        """The chat list is what the operator looks at to find the chat again."""
        tid = chat_store.create_thread("agent:quill")["id"]
        chat_store.append_message(tid, "user", "hi")
        row = next(r for r in chat_store.list_threads() if r["id"] == tid)
        assert row["context_key"] == "agent:quill"
        assert row["scope"] == "agent:quill"


# --- the agent answers ---------------------------------------------------------
class TestTheAgentAnswers:
    def test_an_assistant_agent_provider_is_reached(self, store, registry):
        p = chat_context.get_provider("agent:quill")
        assert isinstance(p, chat_context.AssistantAgentContextProvider)
        assert p.key == "agent:quill"

    def test_its_persona_is_the_registry_definition(self, store, registry):
        """Asserted against the RECORD, not against a string in this file: the
        thing that was broken is that the chat answered from the general
        assistant's persona, which contains neither of these."""
        p = chat_context.get_provider("agent:quill")
        persona = p.persona()
        assert ASSISTANT_REC["name"] in persona
        assert ASSISTANT_REC["purpose"] in persona
        assert ASSISTANT_REC["definition"] in persona
        assert persona != chat_context.ContextProvider().persona()

    def test_its_model_is_the_providers_not_the_general_one(self, store, registry):
        import routes.chat as chat_routes

        t = chat_store.create_thread("agent:quill")
        p = chat_context.get_provider("agent:quill")
        resolved = chat_routes._resolve_model(None, t, p)
        assert resolved == p.default_model

    def test_a_maker_agent_gets_its_tree_and_the_full_toolset(self, store, registry):
        """What the `build:` provider used to do, reached through `agent:` and
        selected by ROLE. A maker is the one role that writes a tree, so it is
        the one that gets a cwd and the agentic path."""
        p = chat_context.get_provider("agent:smith")
        assert isinstance(p, chat_context.MakerAgentContextProvider)
        assert p.key == "agent:smith"
        assert p.dir == registry
        assert chat_context.is_build_provider(p)
        assert "Commit on green." in p.persona()  # AGENTS.md, read into the persona
        # live_context no longer polls anything into the prompt: the scope's
        # goal/plan is the Manifest, a memory note read on demand, not the repo
        # PROJECT.md re-injected each turn (2026-08-25).
        assert p.live_context() == ""
        assert "Build the thing." not in p.live_context()

    def test_an_unroled_agent_is_not_handed_a_working_tree(self, store, registry):
        p = chat_context.get_provider("agent:quill")
        assert not chat_context.is_build_provider(p)
        assert not hasattr(p, "dir")

    def test_an_unknown_slug_degrades_to_general(self, store, registry):
        """A stale binding must not 500 the chat."""
        p = chat_context.get_provider("agent:no-such-agent")
        assert p.key == "general"

    def test_a_retired_agent_degrades_to_general(self, store, registry, monkeypatch):
        from services import agents_registry
        monkeypatch.setattr(agents_registry, "get",
                            lambda s: {**ASSISTANT_REC, "retired": True})
        assert chat_context.get_provider("agent:quill").key == "general"


# --- role, not runtime ---------------------------------------------------------
class TestRoleNotRuntime:
    """`rec["runtime"] == "builder"` was the single most load-bearing use of the
    deleted builder/assistant axis. The split reads `role`, and a record with no
    `runtime` key at all is what the registry actually produces now."""

    def test_a_maker_with_no_runtime_field_still_gets_the_tree(self, store, registry,
                                                              monkeypatch):
        from services import agents_registry
        rec = {k: v for k, v in MAKER_REC.items() if k != "runtime"}
        monkeypatch.setattr(agents_registry, "get", lambda s: rec)
        p = chat_context.get_provider("agent:smith")
        assert isinstance(p, chat_context.MakerAgentContextProvider)

    def test_a_stray_runtime_field_cannot_outrank_the_role(self, store, registry,
                                                          monkeypatch):
        from services import agents_registry
        monkeypatch.setattr(agents_registry, "get",
                            lambda s: {**MAKER_REC, "role": "reviewer"})
        p = chat_context.get_provider("agent:smith")
        assert isinstance(p, chat_context.AssistantAgentContextProvider)

    def test_a_maker_with_no_registered_tree_still_answers(self, store, registry,
                                                           monkeypatch):
        """`build_agents.json` is per-instance execution state and a definition
        can outlive it. The honest degradation is an agent that talks and cannot
        build, not a 500 and not a cwd invented for it."""
        from services import build_agents

        def _missing(slug):
            raise KeyError(slug)

        monkeypatch.setattr(build_agents, "get_agent", _missing)
        p = chat_context.get_provider("agent:smith")
        assert isinstance(p, chat_context.AssistantAgentContextProvider)
        assert p.key == "agent:smith"


# --- the button ----------------------------------------------------------------
class TestTheAgentDetailButton:
    """`AgentDetail.tsx`'s "Start chat with agent", over the wire.

    The body below is the one that page now sends. It used to send
    `context_key: "project:<the agent's project>"` (or `general`) plus
    `participants: ["agent:<slug>"]`, so the button produced a chat with the
    general assistant that merely mentioned the agent's name in its title.
    """

    @pytest.fixture
    def client(self, store):
        app = FastAPI()
        import routes.chat as chat_routes
        app.include_router(chat_routes.router, prefix="/chat")
        return TestClient(app)

    def test_it_creates_a_thread_answered_by_that_agent(self, client, registry):
        r = client.post("/chat/threads", json={
            "context_key": "agent:quill",
            "title": "Chat with Quill",
        })
        assert r.status_code == 200
        created = r.json()
        assert created["context_key"] == "agent:quill"

        # ...and the thread the SERVER holds, re-read, still says so. This is
        # the assertion `_promotion_patch` broke: the create response was built
        # from the already-rewritten meta.
        stored = chat_store.get_thread(created["id"])
        assert stored["context_key"] == "agent:quill"

        # ...and the provider that will answer its turns is the agent.
        provider = chat_context.get_provider(stored["context_key"])
        assert isinstance(provider, chat_context.AssistantAgentContextProvider)
        assert provider.slug == "quill"
        assert ASSISTANT_REC["definition"] in provider.persona()

    def test_the_route_rejects_a_participants_field_silently_no_more(self, client,
                                                                    registry):
        """A stale client sending the old body gets the agent chat it asked for
        rather than a general one; the dead field is simply ignored by the
        model."""
        r = client.post("/chat/threads", json={
            "context_key": "agent:quill",
            "title": "Chat with Quill",
            "participants": ["agent:quill"],
        })
        assert r.status_code == 200
        assert r.json()["context_key"] == "agent:quill"
        assert "participants" not in r.json()

    def test_rescoping_an_existing_chat_onto_an_agent_works(self, client, registry):
        """The other way in: the scope picker. `rescope` validates that the
        resolved provider's key equals the request, which an agent key could
        never satisfy while the provider minted `build:` and the store rewrote
        `agent:`."""
        tid = chat_store.create_thread("general")["id"]
        r = client.post(f"/chat/threads/{tid}/rescope",
                        json={"context_key": "agent:smith"})
        assert r.status_code == 200, r.text
        assert r.json()["context_key"] == "agent:smith"
        assert chat_store.get_thread(tid)["context_key"] == "agent:smith"


# --- build: is deleted ---------------------------------------------------------
class TestBuildIsDeleted:
    def test_nothing_translates_the_old_spelling_any_more(self):
        """The fold is gone, and that is the point. While it existed, both
        spellings worked, so nothing in the tree was ever forced to pick one and
        `build:` kept being written by new code."""
        assert chat_store.canonical_context_key("build:smith") == "build:smith"

    def test_canonicalising_is_now_only_about_the_empty_case(self):
        for key in ("agent:smith", "project:birds", "development"):
            assert chat_store.canonical_context_key(key) == key
        assert chat_store.canonical_context_key("") == "general"
        assert chat_store.canonical_context_key(None) == "general"
        assert chat_store.canonical_context_key("   ") == "general"

    def test_the_old_spelling_reaches_no_agent(self, store, registry):
        """AgentBuild.tsx sends `agent:<slug>` now. A caller still holding the
        old key degrades to `general` rather than being quietly translated, which
        is what makes a leftover call site visible instead of invisible."""
        p = chat_context.get_provider("build:smith")
        assert p.key == "general"

    def test_no_provider_mints_a_build_key(self, store, registry):
        for key in ("agent:smith", "build:smith", "agent:quill"):
            assert not chat_context.get_provider(key).key.startswith("build:")


# --- participants is gone ------------------------------------------------------
class TestParticipantsIsGone:
    def test_the_thread_document_has_no_participants(self, store, registry):
        t = chat_store.create_thread("agent:quill")
        assert "participants" not in t
        assert "participants" not in chat_store.get_thread(t["id"])

    def test_the_summary_row_has_no_participants(self, store, registry):
        tid = chat_store.create_thread("agent:quill")["id"]
        chat_store.append_message(tid, "user", "hi")
        row = next(r for r in chat_store.list_threads() if r["id"] == tid)
        assert "participants" not in row

    def test_the_pg_row_shape_matches_the_file_fold(self, store, registry):
        """The two read paths must agree on the field set, or the chat list
        changes shape depending on whether Postgres answered."""
        from services import chat_list_pg
        row = chat_list_pg._shape_row(
            {"external_id": "x", "context_key": "agent:quill", "title": "t"})
        assert "participants" not in row
        assert row["agent_id"] is None

    def test_create_thread_takes_no_participants(self):
        import inspect
        assert "participants" not in inspect.signature(
            chat_store.create_thread).parameters

    def test_the_create_route_takes_no_participants(self):
        from routes.chat import CreateThread
        assert "participants" not in CreateThread.model_fields

    def test_it_is_gone_from_the_source_tree(self):
        """The grep. A field with no readers regrows if a copy of it is left
        anywhere, which is the whole thesis of the coherence overhaul.

        Scoped to CODE, not to prose. The comments recording where the field
        used to be are the record this overhaul runs on and are meant to stay;
        what must be gone is every identifier, dict key, column and prop. The
        pattern below is the code shapes it had, and prose naming it in
        backticks does not match any of them."""
        pattern = (r'_participants'
                   r'|["'"'"']participants["'"'"']'
                   r'|\bparticipants\s*[:=]'
                   r'|\.participants\b')
        out = subprocess.run(
            ["git", "grep", "-nE", pattern, "--",
             "backend/services", "backend/routes", "backend/db",
             "frontend/src", "packages"],
            cwd=REPO, capture_output=True, text=True)
        offenders = []
        for line in out.stdout.splitlines():
            _path, _, rest = line.partition(":")
            _lineno, _, code = rest.partition(":")
            if code.strip().startswith(("#", "*", "//")):
                continue
            offenders.append(line)
        assert offenders == [], (
            "`participants` is still live code in:\n" + "\n".join(offenders))


# --- the mention path is untouched ---------------------------------------------
class TestMentionIsUnchanged:
    """D1/D2/D3 are operator-ratified. A mention is the routing tool, not a
    second model, and nothing here was allowed to change it."""

    def test_a_mention_still_parses_and_routes(self):
        from services import mentions
        slug, start, end = mentions.find_first("hey @quill can you look at this")
        assert slug == "quill"
        assert start >= 0 and end > start

    def test_an_agent_session_is_per_agent_and_per_thread(self, store, registry):
        """The contradiction fixed in T2 was a COMMENT, not the behaviour:
        `routes/chat.py` claimed mentions were "deliberately stateless" while
        calling `ensure_agent_session` three hundred lines below. The behaviour
        is the truth and it stays."""
        a = chat_store.create_thread("general")["id"]
        b = chat_store.create_thread("general")["id"]
        sid_a, new_a = chat_store.ensure_agent_session(a, "quill")
        sid_a2, new_a2 = chat_store.ensure_agent_session(a, "quill")
        sid_b, _ = chat_store.ensure_agent_session(b, "quill")
        sid_other, _ = chat_store.ensure_agent_session(a, "smith")
        assert new_a and not new_a2
        assert sid_a == sid_a2, "the same agent in the same thread continues"
        assert sid_a != sid_b, "two chats that share a speaker stay apart"
        assert sid_a != sid_other, "two agents in one chat stay apart"

    def test_both_sides_of_the_contradiction_now_say_the_same_thing(self):
        """One of the two comments was false. Pin the surviving claim so the
        false one cannot come back as a "clarification"."""
        import routes.chat as chat_routes
        doc = chat_routes._start_agent_mention_turn.__doc__ or ""
        assert "A MENTION IS NOT STATELESS" in doc
        assert "deliberately stateless" not in doc.lower().replace(
            "deliberately stateless per mention", "")
