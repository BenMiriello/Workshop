"""Compaction leaves something behind that outlives the thread it happened in.

THE DEFECT THESE PIN, measured 2026-08-23. A fold made one Haiku call asking
for "a running summary", stored the paragraph it got back on the thread record,
and stopped. Two consequences:

  1. The shape of what survived was whatever the summarizer felt like writing.
     Free prose loses the things nobody thinks to write down: the REASON a
     decision was made (so the next session re-litigates or silently reverses
     it) and the shortcuts taken on the way (so a shortcut becomes
     indistinguishable from a design choice, which is how this repo grew the
     parallel systems it is now removing).
  2. It lived only inside that thread. A NEW chat in the same scope inherited
     nothing at all. The two hand-written `docs/platform-redesign/HANDOFF-*.md`
     files exist because two agents happened to remember to write one.

So: the summarizer is asked for six fixed sections, the result is saved as the
scope's `working-state` note (one per scope, updated in place, in the store
that already exists), and it is injected into every chat in that scope.

Every one of these is best-effort in the direction that matters: a handoff that
cannot be written must not fail a compaction, and a compaction that fails must
not fail a turn.
"""
from __future__ import annotations

import asyncio

import pytest

from services import chat_context, chat_store, knowledge, ownership


STRUCTURED = """## Objective
- Give compaction a durable, scope-level handoff.

## Decisions and why
- The handoff is a NOTE, because a twelfth memory substrate would be absurd.

## Done
- backend/services/knowledge.py write_working_state.

## Not done
- Nothing.

## Shortcuts taken and what they owe
- The bound is a char proxy, not a tokenizer; it owes a real count if it bites.

## Open questions
- Whether learnings stay a first-order concept. Owner: the operator.
"""


@pytest.fixture()
def scoped(tmp_path, monkeypatch):
    """An isolated workshop: own runtime (chats, notes), own projects root.

    Notes for `workshop` resolve through `ownership.workspace_path`, and chats
    through `ownership.user_runtime_dir`, so pinning `_RUNTIME` at a tmp dir is
    what keeps a test fold from writing a working-state note into the real
    tracked `shared/knowledge/`.
    """
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from wsplatform import paths
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    for slug in ("alpha", "beta"):
        (tmp_path / "projects" / slug).mkdir(parents=True, exist_ok=True)
    ownership.set_current_owner(4242)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


def _chatty(context_key: str = "general") -> str:
    """A thread with enough conversation in it to clear the fold's guardrails
    (see `_fold_summary`: at least 2 messages and more than one keep-budget's
    worth of tokens, or it correctly folds nothing)."""
    tid = chat_store.create_thread(context_key)["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"OLD-{i} " + ("filler " * 400))
        chat_store.append_message(tid, "assistant", f"reply-{i} " + ("filler " * 400))
    chat_store.append_message(tid, "user", "RECENT the latest thing")
    return tid


def _stub_summarizer(monkeypatch, text: str = STRUCTURED):
    from routes import chat as chat_routes
    monkeypatch.setattr(chat_routes, "_summarize_sync", lambda prev, msgs: text)
    return chat_routes


async def _fold_now(tid: str):
    """Compute and land in one step, as the route does on a quiet thread."""
    from routes import chat as chat_routes
    fold_n, summary, through, err = await chat_routes._fold_summary(
        tid, chat_store.get_thread(tid))
    if not err and fold_n > 0:
        chat_routes._land_fold(tid, summary, through)
    return fold_n, err


def _prompt(tid: str) -> str:
    from routes import chat as chat_routes
    t = chat_store.get_thread(tid)
    provider = chat_context.get_provider(t.get("context_key"))
    prompt, _ = asyncio.run(chat_routes._assemble_full_prompt(t, provider, "next?"))
    return prompt


class TestTheSummarizerIsAskedForAShape:
    """A structured prompt is the only thing standing between this and prose."""

    def test_the_prompt_names_all_six_headings_verbatim(self):
        from routes import chat as chat_routes
        p = chat_routes._summary_prompt("", [{"role": "user", "content": "hi"}])
        for heading in knowledge.WORKING_STATE_SECTIONS:
            assert f"## {heading}" in p, heading

    def test_the_prompt_asks_for_the_reason_behind_each_decision(self):
        """The highest-value section, and the one a summarizer drops first: a
        decision without its reason gets re-litigated or silently reversed."""
        from routes import chat as chat_routes
        p = chat_routes._summary_prompt("", [{"role": "user", "content": "hi"}])
        assert "THE MOST IMPORTANT SECTION" in p
        assert "re-litigated" in p

    def test_the_prompt_keeps_the_instructions_that_already_worked(self):
        from routes import chat as chat_routes
        p = chat_routes._summary_prompt("", [{"role": "user", "content": "hi"}])
        assert "no em-dashes" in p and "no emojis" in p
        assert "identifiers" in p
        assert "operator's preferences" in p or "operator's stated preferences" in p

    def test_the_earlier_state_is_fed_back_in_so_a_fold_updates_it(self):
        from routes import chat as chat_routes
        p = chat_routes._summary_prompt("PREVIOUS STATE", [{"role": "user", "content": "hi"}])
        assert "PREVIOUS STATE" in p
        assert "UPDATED working state" in p

    def test_it_stays_one_haiku_call_with_no_tools(self, monkeypatch):
        """Compaction runs on a live thread while the operator waits. Two calls,
        or an agentic one, is what made /compact look hung."""
        from routes import chat as chat_routes
        from services import llm_router
        seen = []

        def fake_call(prompt, **kw):
            seen.append(kw)
            return {"text": STRUCTURED}

        monkeypatch.setattr(llm_router, "call_cloud", fake_call)
        out = chat_routes._summarize_sync("", [{"role": "user", "content": "hi"}])
        assert out.startswith("## Objective")
        assert len(seen) == 1
        assert seen[0]["tools_disabled"] is True
        assert seen[0]["model"] == llm_router.TIER_HAIKU


class TestAFoldWritesTheScopesWorkingState:
    def test_the_sections_survive_the_round_trip_to_the_note(self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch)
        tid = _chatty()
        folded, err = asyncio.run(_fold_now(tid))
        assert err == "" and folded > 0
        body = knowledge.get_note("workshop", knowledge.WORKING_STATE_NOTE_ID)
        assert body
        for heading in knowledge.WORKING_STATE_SECTIONS:
            assert f"## {heading}" in body, heading
        assert "twelfth memory substrate" in body

    def test_the_note_says_what_it_is_so_it_is_not_mistaken_for_an_ordinary_one(
            self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty()))
        body = knowledge.get_note("workshop", knowledge.WORKING_STATE_NOTE_ID)
        # No `# ` title line: the Memory page prints the note's own title
        # above the body, so an H1 here renders as a second, larger, duplicate
        # heading. The first sentence carries the fact instead.
        assert not body.lstrip().startswith("# ")
        assert body.lstrip().startswith("Written automatically when a chat in this scope compacts")
        assert "overwritten by" in body

    def test_it_carries_its_provenance(self, scoped, monkeypatch):
        """A model wrote this, in a particular chat. Both are already fields on
        a note; neither was being filled by anything on the compaction path."""
        _stub_summarizer(monkeypatch)
        tid = _chatty()
        asyncio.run(_fold_now(tid))
        raw = (knowledge.scope_dir("workshop")
               / f"{knowledge.WORKING_STATE_NOTE_ID}.md").read_text()
        assert knowledge.note_chat(raw) == tid
        assert raw.startswith("<!-- author: derived:")

    def test_a_second_fold_updates_the_note_instead_of_adding_a_second_one(
            self, scoped, monkeypatch):
        """One note per scope. A pile of dated handoffs is a pile nobody reads,
        and the operator asked for the CURRENT working state."""
        _stub_summarizer(monkeypatch, STRUCTURED)
        asyncio.run(_fold_now(_chatty()))
        second = STRUCTURED.replace("- Nothing.", "- The second fold's leftovers.")
        _stub_summarizer(monkeypatch, second)
        asyncio.run(_fold_now(_chatty()))
        ids = [n["id"] for n in knowledge.list_notes("workshop")]
        assert ids.count(knowledge.WORKING_STATE_NOTE_ID) == 1
        body = knowledge.get_note("workshop", knowledge.WORKING_STATE_NOTE_ID)
        assert "The second fold's leftovers." in body

    def test_two_scopes_keep_two_separate_working_states(self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch, STRUCTURED.replace("Objective\n-", "Objective\n- ALPHA"))
        asyncio.run(_fold_now(_chatty("project:alpha")))
        _stub_summarizer(monkeypatch, STRUCTURED.replace("Objective\n-", "Objective\n- BETA"))
        asyncio.run(_fold_now(_chatty("project:beta")))
        assert "ALPHA" in (knowledge.get_note("project:alpha",
                                              knowledge.WORKING_STATE_NOTE_ID) or "")
        assert "BETA" in (knowledge.get_note("project:beta",
                                             knowledge.WORKING_STATE_NOTE_ID) or "")
        assert "ALPHA" not in (knowledge.get_note("project:beta",
                                                  knowledge.WORKING_STATE_NOTE_ID) or "")


    def test_the_index_cap_never_demotes_it(self, scoped):
        """Demotion moves a note into `archive/`, where the read path cannot
        see it. A scope that crossed the 200-line index cap would silently stop
        handing new chats its working state."""
        knowledge.write_working_state("workshop", STRUCTURED, author="derived:x")
        for i in range(250):
            asyncio.run(knowledge.put_note("workshop", f"filler-{i}", f"Filler {i}", "body"))
        knowledge.enforce_index_cap("workshop")
        text, _chat = knowledge.read_working_state("workshop")
        assert "twelfth memory substrate" in text


class TestNothingHereMayFailAnythingElse:
    def test_a_failing_note_write_does_not_fail_the_fold(self, scoped, monkeypatch):
        """The operator pressed compact to reclaim context. That has already
        happened by the time the handoff is written; a handoff that could not be
        filed is a log line, not a failed operation."""
        from routes import chat as chat_routes
        _stub_summarizer(monkeypatch)

        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(knowledge, "write_working_state", boom)
        tid = _chatty()
        folded, err = asyncio.run(_fold_now(tid))
        assert err == "" and folded > 0
        # The fold itself landed: the summary is on the thread and the cut moved.
        t = chat_store.get_thread(tid)
        assert (t.get("summary") or "").startswith("## Objective")
        assert int(t.get("cleared_through", 0)) > 0
        assert knowledge.get_note("workshop", knowledge.WORKING_STATE_NOTE_ID) is None
        assert chat_routes is not None

    def test_a_failing_fold_does_not_fail_the_turn(self, scoped, monkeypatch):
        """The summarizer is a model call and it can fail. It is REPORTED, the
        thread is untouched, and the next prompt still assembles."""
        from routes import chat as chat_routes

        def boom(prev, msgs):
            raise RuntimeError("the summarizer fell over")

        monkeypatch.setattr(chat_routes, "_summarize_sync", boom)
        tid = _chatty()
        folded, err = asyncio.run(_fold_now(tid))
        assert folded == 0 and "the summarizer fell over" in err
        assert not (chat_store.get_thread(tid).get("summary") or "")
        assert "RECENT the latest thing" in _prompt(tid)

    def test_an_unreadable_note_store_does_not_break_the_prompt(self, scoped, monkeypatch):
        def boom(scope):
            raise OSError("gone")

        monkeypatch.setattr(knowledge, "read_working_state", boom)
        assert "RECENT the latest thing" in _prompt(_chatty())


class TestANewChatInTheScopeContinuesFromIt:
    """The half that makes this worth building: today's summary helps only the
    thread that produced it."""

    def test_a_different_chat_in_the_same_scope_is_handed_the_working_state(
            self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty()))
        fresh = chat_store.create_thread("general")["id"]
        chat_store.append_message(fresh, "user", "what were we doing?")
        prompt = _prompt(fresh)
        assert "=== WORKING STATE (this scope, from the last compaction) ===" in prompt
        assert "twelfth memory substrate" in prompt

    def test_a_chat_in_a_different_scope_is_not(self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty("project:alpha")))
        other = chat_store.create_thread("project:beta")["id"]
        chat_store.append_message(other, "user", "unrelated work")
        prompt = _prompt(other)
        assert "=== WORKING STATE" not in prompt
        assert "twelfth memory substrate" not in prompt

    def test_the_chat_that_wrote_it_is_not_handed_it_twice(self, scoped, monkeypatch):
        """It already carries the same text verbatim as its own compaction
        summary. Paying the budget twice for one body of text is how a window
        fills up for no reason."""
        _stub_summarizer(monkeypatch)
        tid = _chatty()
        asyncio.run(_fold_now(tid))
        prompt = _prompt(tid)
        assert "=== EARLIER IN THIS CONVERSATION (summary) ===" in prompt
        assert "=== WORKING STATE" not in prompt
        assert prompt.count("twelfth memory substrate") == 1

    def test_after_a_clear_wipes_the_summary_the_note_is_injected_again(
            self, scoped, monkeypatch):
        """/clear drops the summary, so the note becomes the only surviving
        copy. Suppressing it there would delete the state outright."""
        _stub_summarizer(monkeypatch)
        tid = _chatty()
        asyncio.run(_fold_now(tid))
        chat_store.clear_thread_context(tid)
        chat_store.append_message(tid, "user", "carry on")
        assert not (chat_store.get_thread(tid).get("summary") or "")
        assert "=== WORKING STATE" in _prompt(tid)

    def test_the_injected_block_says_which_of_it_and_the_digest_owns_what(
            self, scoped, monkeypatch):
        """Two neighbouring blocks that both look like "ongoing work" will get
        double-counted unless one of them says who owns what."""
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty()))
        fresh = chat_store.create_thread("general")["id"]
        chat_store.append_message(fresh, "user", "hello")
        prompt = _prompt(fresh)
        assert "does NOT own ticket or job status" in prompt
        assert "owns the reasoning" in prompt


class TestItIsBounded:
    """A running state that is re-fed to itself on every fold is exactly the
    shape of thing that grows without bound."""

    def test_an_over_long_state_is_trimmed_at_the_write(self, scoped, monkeypatch):
        fat = STRUCTURED.replace(
            "## Done\n", "## Done\n" + "".join(f"- item {i} " + "x" * 200 + "\n"
                                               for i in range(200)))
        assert len(fat) > knowledge.WORKING_STATE_MAX_CHARS * 5
        _stub_summarizer(monkeypatch, fat)
        asyncio.run(_fold_now(_chatty()))
        text, _chat = knowledge.read_working_state("workshop")
        assert len(text) <= knowledge.WORKING_STATE_MAX_CHARS

    def test_trimming_takes_from_the_fattest_section_and_keeps_every_heading(self):
        """Not a tail truncation: "Shortcuts taken" and "Open questions" are
        last, and they are the two nobody can reconstruct."""
        fat = STRUCTURED.replace(
            "## Done\n", "## Done\n" + "".join(f"- item {i} " + "x" * 200 + "\n"
                                               for i in range(200)))
        out = knowledge._bound_working_state(fat)
        assert len(out) <= knowledge.WORKING_STATE_MAX_CHARS
        for heading in knowledge.WORKING_STATE_SECTIONS:
            assert f"## {heading}" in out, heading
        assert "Whether learnings stay a first-order concept" in out
        assert "the bound is a char proxy" in out.lower()

    def test_a_trim_is_declared_rather_than_silent(self):
        fat = STRUCTURED + "\n".join(f"- filler {i} " + "y" * 200 for i in range(200))
        out = knowledge._bound_working_state(fat)
        assert knowledge._WORKING_STATE_TRIM_MARKER in out

    def test_a_state_with_no_headings_at_all_is_still_bounded(self):
        """A hand-edited note, or a summarizer that ignored the shape. It still
        may not spend an unbounded slice of every prompt."""
        out = knowledge._bound_working_state("z" * 100_000)
        assert len(out) <= knowledge.WORKING_STATE_MAX_CHARS

    def test_the_read_path_bounds_it_too(self, scoped):
        """The operator can edit this note by hand, and any agent with note
        tools can write it. The prompt's budget is not theirs to spend."""
        asyncio.run(knowledge.put_note(
            "workshop", knowledge.WORKING_STATE_NOTE_ID, "Working state",
            "## Objective\n" + "- " + "q" * 100_000 + "\n"))
        text, _chat = knowledge.read_working_state("workshop")
        assert len(text) <= knowledge.WORKING_STATE_MAX_CHARS

    def test_it_does_not_spend_the_knowledge_index_budget(self):
        """The layered index gets 500 tokens for ALL of a scope's note titles.
        One handoff would empty it."""
        assert (knowledge.WORKING_STATE_MAX_CHARS // 4
                > knowledge.DEFAULT_LAYERED_INDEX_TOKEN_BUDGET)

    def test_it_stays_well_under_the_verbatim_tail_compaction_keeps(self):
        """A summary of old work that outweighs the live conversation is
        inverted priorities, and it sits higher in the prompt."""
        from routes import chat as chat_routes
        assert (knowledge.WORKING_STATE_MAX_CHARS // 4
                < chat_routes._COMPACT_KEEP_TAIL_TOKENS / 2)


class TestTheOperatorCanSeeAndEditIt:
    def test_it_is_listed_in_the_scopes_index_like_any_other_note(
            self, scoped, monkeypatch):
        """It is a note, so the Memory page lists it with no new page and no
        new route. The index line is what that page reads."""
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty()))
        rows = {n["id"]: n for n in knowledge.list_notes("workshop")}
        row = rows[knowledge.WORKING_STATE_NOTE_ID]
        assert row["title"] == knowledge.WORKING_STATE_TITLE
        assert "handoff" in row["hook"]

    def test_an_operator_edit_is_what_the_next_chat_reads(self, scoped, monkeypatch):
        _stub_summarizer(monkeypatch)
        asyncio.run(_fold_now(_chatty()))
        asyncio.run(knowledge.put_note(
            "workshop", knowledge.WORKING_STATE_NOTE_ID,
            knowledge.WORKING_STATE_TITLE,
            "## Objective\n- CORRECTED BY HAND\n", author="operator"))
        fresh = chat_store.create_thread("general")["id"]
        chat_store.append_message(fresh, "user", "hello")
        assert "CORRECTED BY HAND" in _prompt(fresh)
