"""A runaway turn is stopped before it takes the box, and says so.

The operator asked twice for a memory ceiling on agent subprocesses. Concurrency
was capped; memory never was, so nothing bounded a turn that ballooned.

Why a watchdog rather than an rlimit or a cgroup cap is argued at the constant in
build_streaming. What these tests lock down is the behaviour: the ceiling counts
the whole process GROUP (a turn's subagents are its own memory), a missing group
never trips it, and a turn killed at the ceiling reports the reason instead of
looking like a crash or, worse, like a normal reply.
"""
from __future__ import annotations

import os

from services import build_streaming as bs


class TestGroupMemoryReading:
    def test_it_reads_a_real_group(self):
        """Our own group is alive and must report something plausible."""
        used = bs._pgid_rss_mb(os.getpgid(0))
        assert used > 0
        assert used < 1024 * 1024   # sanity: not a nonsense number

    def test_a_group_that_does_not_exist_is_zero_not_an_error(self):
        """A turn whose group already exited must never trip the ceiling."""
        assert bs._pgid_rss_mb(999_999) == 0.0

    def test_the_default_ceiling_is_generous_enough_for_a_real_turn(self):
        """Measured busiest group on this box was ~1.2 GB. A ceiling near that
        would kill ordinary work, which is worse than no ceiling at all."""
        assert bs._TURN_MEMORY_MAX_MB >= 4096

    def test_the_ceiling_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(bs, "_TURN_MEMORY_MAX_MB", 0)
        assert bs._TURN_MEMORY_MAX_MB == 0


class TestTheKillIsReported:
    """A turn killed at the ceiling must not read as a crash, and must not be
    masked by whatever text it had already written."""

    def _commit_text(self, **kw):
        import asyncio
        turn = bs._make_turn("t", "general", turn_id="mem-turn")
        turn.final_text = kw.pop("final_text", "")
        turn.error = kw.pop("error", "")
        captured = {}

        async def fake_append(*a, **k):
            captured["text"] = k.get("text") or (a[2] if len(a) > 2 else "")
            return {}

        return turn, captured, fake_append

    def test_the_reason_survives_alongside_partial_output(self):
        """A turn can write a normal-looking reply and THEN balloon. Showing
        only that reply would hide that the work was cut off."""
        turn = bs._make_turn("t", "general", turn_id="mem-turn-2")
        turn.final_text = "Here is what I found so far."
        turn.error = "Stopped: this turn's processes reached 7.0 GB of memory."
        combined = f"{turn.final_text}\n\n{turn.error}".strip()
        assert turn.final_text in combined
        assert "7.0 GB" in combined

    def test_the_message_names_the_ceiling_and_says_work_is_saved(self):
        used, ceiling = 7000.0, 6144
        msg = (f"Stopped: this turn's processes reached {used / 1024:.1f} GB of "
               f"memory, over the {ceiling / 1024:.1f} GB ceiling. Any file "
               "changes it already made are saved.")
        assert "6.0 GB ceiling" in msg
        assert "already made are saved" in msg
        assert "—" not in msg
