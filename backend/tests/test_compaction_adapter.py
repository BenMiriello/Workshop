"""The provider-agnostic compaction seam (`services.compaction_adapter`).

Workshop runs one provider today (the Claude Code CLI, headless) and this
adapter wraps the existing fold/detach functions in `routes.chat` rather than
reimplementing them, so these tests check the wrapping is honest: the
capabilities it reports match the facts the CLI docstring already established
(no reachable native manual compaction from headless mode), and its methods
actually delegate rather than silently no-op.
"""
import asyncio

import pytest

from services import chat_store, ownership
from services.compaction_adapter import ClaudeCLIAdapter, EvictionResult


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


class TestCapabilitiesReportTheRealCLIFacts:
    def test_native_manual_is_false(self):
        """The headless `/compact` command is confirmed dead (see
        `routes.chat.compact_thread`'s docstring, measured 2026-08-12): a
        leading slash over stream-json is parsed into a local-command block
        nothing executes, and there is no control-protocol request for it
        either. This adapter must not claim otherwise."""
        adapter = ClaudeCLIAdapter(None, None, None)
        caps = adapter.capabilities()
        assert caps["native_manual"] is False

    def test_native_auto_is_true(self):
        """Unlike `native_manual`, automatic compaction inside a `--resume`d
        session is a CONFIRMED fact, not an open question:
        `services.build_streaming` detects the CLI's own `compact_boundary`
        meta event mid-turn and writes a marker for it
        (`chat_store.note_auto_compact`), and
        `test_an_auto_compact_mid_work_keeps_its_real_reply` pins the CLI
        really doing this. Reporting `False` here would contradict the rest
        of the codebase, which already treats it as fact."""
        adapter = ClaudeCLIAdapter(None, None, None)
        caps = adapter.capabilities()
        assert caps["native_auto"] is True

    def test_the_rest_of_the_capability_set_is_also_honest(self):
        adapter = ClaudeCLIAdapter(None, None, None)
        caps = adapter.capabilities()
        # No CLI primitive does surgical tool-output eviction.
        assert caps["can_evict_tool_output"] is False
        assert caps["preserves_cache_prefix"] is False

    def test_capabilities_is_a_plain_dict_not_a_live_read(self):
        """Static facts, not a probe -- calling it twice must not touch the
        network or the filesystem and must always agree with itself."""
        adapter = ClaudeCLIAdapter(None, None, None)
        assert adapter.capabilities() == adapter.capabilities()


class TestTheAdapterActuallyDelegates:
    """Not a stub: `compact`/`clear`/`evict_tool_output` call the injected
    functions rather than reimplementing or faking their behavior."""

    def test_compact_calls_the_injected_fold_summary(self):
        calls = []

        async def fake_fold_summary(thread_id, thread):
            calls.append((thread_id, thread))
            return (3, "a summary", 5, "")

        adapter = ClaudeCLIAdapter(fake_fold_summary, None, None)
        result = asyncio.run(adapter.compact("tid-1", {"some": "thread"}))
        assert calls == [("tid-1", {"some": "thread"})]
        assert (result.folded, result.summary, result.folded_through, result.error) \
            == (3, "a summary", 5, "")

    def test_evict_tool_output_is_a_real_no_op_matching_its_own_capability(self):
        """`can_evict_tool_output` says False; this is what makes that true
        rather than aspirational -- it costs nothing and evicts nothing,
        which is the honest answer for a provider with no such primitive."""
        adapter = ClaudeCLIAdapter(None, None, None)
        result = asyncio.run(adapter.evict_tool_output("tid-1", {}))
        assert result == EvictionResult(evicted=0)

    def test_clear_calls_the_injected_detach(self, owned):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "m0")
        t = chat_store.get_thread(tid)

        calls = []

        def fake_detach(thread_id, thread, provider):
            calls.append((thread_id, thread is t, provider))
            return True

        adapter = ClaudeCLIAdapter(None, None, fake_detach)
        result = asyncio.run(adapter.clear(tid, t))
        assert result.session_reset is True
        assert calls == [(tid, True, calls[0][2])]
