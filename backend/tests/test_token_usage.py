"""One module owns the parse from a vendor usage block to named quantities.

The four questions cannot share a number, and the tests that matter here are the
ones that pin WHY:

  - frames sharing one API message id are one call, not several (measured
    inflation on real journals in this repo: 2.07x to 2.88x);
  - occupancy reads the main conversation only and is never summed, while spend
    and effective count delegates too;
  - effective() reproduces, to the token, the weighted arithmetic scripts/ralph.sh
    has computed inline, so moving it here cannot change a worker's numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.token_usage import (CACHE_READ_WEIGHT, TurnMeter, Usage,
                                  effective, occupancy, parse, spend)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "usage_journal.jsonl"


def _journal() -> list[dict]:
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]


def _feed(meter: TurnMeter) -> TurnMeter:
    """Drive the meter from the fixture journal the way a reader would."""
    for rec in _journal():
        if rec.get("type") == "result":
            meter.observe_result(rec.get("usage"), rec.get("total_cost_usd"))
            continue
        msg = rec.get("message") or {}
        if not isinstance(msg, dict) or not msg.get("usage"):
            continue
        meter.observe(msg.get("id"), msg["usage"],
                      parent=rec.get("parentUuid") if rec.get("isSidechain") else None)
    return meter


class TestParse:
    def test_it_reads_the_four_components(self):
        u = parse({"input_tokens": 1, "cache_read_input_tokens": 2,
                   "cache_creation_input_tokens": 3, "output_tokens": 4})
        assert u == Usage(input=1, cache_read=2, cache_creation=3, output=4)

    @pytest.mark.parametrize("junk", [None, "", 0, [], "usage", {"nope": 1}])
    def test_garbage_yields_zeros_rather_than_raising(self, junk):
        """Metering must never be the thing that costs a turn."""
        assert parse(junk) == Usage()

    def test_a_field_that_arrives_as_a_string_still_counts(self):
        assert parse({"input_tokens": "12"}).input == 12

    def test_a_negative_count_is_clamped_not_trusted(self):
        assert parse({"output_tokens": -5}).output == 0

    def test_unknown_fields_are_ignored(self):
        """Real usage blocks carry service_tier, server_tool_use and more."""
        u = parse({"input_tokens": 5, "service_tier": "standard",
                   "server_tool_use": {"web_search_requests": 3}})
        assert u == Usage(input=5)


class TestTheQuantitiesAreDifferentNumbers:
    U = Usage(input=100, cache_read=50_000, cache_creation=2_000, output=400)

    def test_occupancy_excludes_output_because_output_is_not_context(self):
        assert occupancy(self.U) == 100 + 50_000 + 2_000

    def test_spend_counts_all_four_at_parity(self):
        assert spend(self.U) == 100 + 50_000 + 2_000 + 400

    def test_effective_discounts_cache_reads(self):
        assert effective(self.U) == 100 + 5_000 + 2_000 + 400

    def test_they_are_genuinely_three_different_numbers(self):
        """If any two of these agree on a realistic block, one of them is wrong."""
        assert len({occupancy(self.U), spend(self.U), effective(self.U)}) == 3

    def test_the_gap_between_spend_and_effective_is_the_cache_read_weight(self):
        assert spend(self.U) - effective(self.U) == 50_000 - 5_000


class TestEffectiveMatchesTheWorker:
    """scripts/ralph.sh computes this inline per iteration. Moving the
    arithmetic here must not change a single worker number, so this reproduces
    the shell expression rather than restating the intent."""

    @staticmethod
    def _ralph(u: dict) -> int:
        # Transcribed from scripts/ralph.sh: the weighted term is floored on its
        # OWN before the sum, not after it.
        return (int(u.get("input_tokens", 0))
                + int(0.1 * int(u.get("cache_read_input_tokens", 0)))
                + int(u.get("cache_creation_input_tokens", 0))
                + int(u.get("output_tokens", 0)))

    @pytest.mark.parametrize("block", [
        {"input_tokens": 108, "cache_read_input_tokens": 5_339_452,
         "cache_creation_input_tokens": 247_716, "output_tokens": 372},
        {"input_tokens": 0, "cache_read_input_tokens": 7, "output_tokens": 1},
        {"input_tokens": 3, "cache_read_input_tokens": 9},
        {"cache_read_input_tokens": 15},
        {},
    ])
    def test_it_reproduces_the_shell_expression(self, block):
        assert effective(parse(block)) == self._ralph(block)

    def test_the_truncation_ordering_is_load_bearing(self):
        """cache_read = 9 floors to 0 on its own and would not if the sum were
        floored at the end. That is why the ordering is pinned, not just the
        formula."""
        assert effective(Usage(cache_read=9)) == 0
        assert int(CACHE_READ_WEIGHT * 9) == 0

    def test_per_call_then_sum_is_not_the_same_as_sum_then_weight(self):
        """The documented reason effective() is applied per call: truncation
        does not distribute, and per-call is what the worker's numbers were
        built from."""
        calls = [Usage(cache_read=9)] * 5
        per_call = sum(effective(c) for c in calls)
        summed = effective(Usage(cache_read=45))
        assert per_call == 0 and summed == 4


class TestDeduplicationByMessageId:
    def test_three_frames_sharing_one_id_count_once(self):
        """The defect this module exists to stop. Real journals in this repo run
        2.07x to 2.88x inflated by counting frames instead of calls."""
        m = TurnMeter()
        block = {"input_tokens": 120, "cache_read_input_tokens": 40_000,
                 "cache_creation_input_tokens": 8_000, "output_tokens": 300}
        for _ in range(3):
            m.observe("msg_aaa", block)
        assert m.frames == 3 and m.calls == 1
        assert m.totals == parse(block)
        assert m.spend == spend(parse(block))

    def test_the_fixture_journal_has_more_frames_than_calls(self):
        """Guards the fixture itself: if it ever stops containing a duplicate,
        the test above is still green and proves nothing about real input."""
        ids = [(r.get("message") or {}).get("id") for r in _journal()
               if isinstance(r.get("message"), dict) and (r["message"].get("usage"))]
        assert len(ids) > len(set(ids))

    def test_a_frame_with_no_id_is_always_counted(self):
        """It cannot be deduplicated, so it is counted. An undercount is
        invisible; the overcount this class exists to stop is the bounded one."""
        m = TurnMeter()
        for _ in range(3):
            m.observe(None, {"input_tokens": 10})
        assert m.calls == 3 and m.totals.input == 30

    def test_distinct_ids_still_accumulate(self):
        m = TurnMeter()
        m.observe("a", {"input_tokens": 10})
        m.observe("b", {"input_tokens": 5})
        assert m.calls == 2 and m.totals.input == 15


class TestParentAndDelegatesAreTreatedDifferently:
    def test_occupancy_ignores_a_delegate(self):
        """A subagent has its own context window; letting it set the meter would
        show the operator a number that is not about their conversation."""
        m = TurnMeter()
        m.observe("main", {"input_tokens": 10, "cache_read_input_tokens": 1_000})
        m.observe("sub", {"input_tokens": 10, "cache_read_input_tokens": 900_000},
                  parent="main")
        assert m.occupancy == 1_010

    def test_spend_and_effective_include_a_delegate(self):
        """Its tokens are really spent."""
        m = TurnMeter()
        m.observe("main", {"input_tokens": 10})
        m.observe("sub", {"input_tokens": 7}, parent="main")
        assert m.totals.input == 17
        assert m.spend == 17
        assert m.effective == 17

    def test_occupancy_is_the_latest_main_call_not_the_max(self):
        """The case the quantity exists for: a compaction shrinks the window,
        and a max would report the pre-compaction peak forever."""
        m = TurnMeter()
        m.observe("a", {"input_tokens": 0, "cache_read_input_tokens": 500_000})
        m.observe("b", {"input_tokens": 0, "cache_read_input_tokens": 12_000})
        assert m.occupancy == 12_000

    def test_occupancy_grows_with_the_window_on_an_ordinary_turn(self):
        """And on a turn that only grows, latest and max agree, which is the
        case ticket 189b9e0e's wording describes."""
        m = TurnMeter()
        m.observe("a", {"cache_read_input_tokens": 10_000})
        m.observe("b", {"cache_read_input_tokens": 60_000})
        assert m.occupancy == 60_000

    def test_a_repeated_frame_does_not_move_occupancy_backwards(self):
        m = TurnMeter()
        m.observe("a", {"cache_read_input_tokens": 10_000})
        m.observe("b", {"cache_read_input_tokens": 60_000})
        m.observe("a", {"cache_read_input_tokens": 10_000})
        assert m.occupancy == 60_000

    def test_a_turn_with_only_delegates_reports_no_occupancy(self):
        m = TurnMeter()
        m.observe("sub", {"cache_read_input_tokens": 5_000}, parent="main")
        assert m.occupancy == 0


class TestAgainstTheFixtureJournal:
    def test_the_call_count_is_deduplicated(self):
        m = _feed(TurnMeter())
        assert m.frames == 7
        assert m.calls == 4  # msg_aaa, msg_bbb, msg_sub, msg_ccc

    def test_totals_count_each_call_once_including_the_delegate(self):
        m = _feed(TurnMeter())
        assert m.totals == Usage(
            input=120 + 90 + 70 + 60,
            cache_read=40_000 + 61_000 + 9_000 + 12_000,
            cache_creation=8_000 + 1_200 + 400 + 900,
            output=300 + 450 + 260 + 210,
        )

    def test_occupancy_is_the_last_main_call_after_the_compaction(self):
        m = _feed(TurnMeter())
        assert m.occupancy == 60 + 12_000 + 900

    def test_the_result_envelope_is_recorded_beside_the_frames_not_instead(self):
        """Deliberately not folded in with an `or`: on the one turn where both
        were present they disagreed by 3x and the reason is unverified, so a row
        must be able to say which number it used."""
        m = _feed(TurnMeter())
        assert m.result_cost_usd == pytest.approx(1.83)
        assert m.result_totals == Usage(input=270, cache_read=100_000,
                                        cache_creation=9_500, output=960)
        assert m.result_totals != m.totals

    def test_a_record_with_no_usage_is_skipped(self):
        m = _feed(TurnMeter())
        assert "msg_none" not in m._calls


class TestUsageArithmetic:
    def test_components_add(self):
        assert Usage(input=1, output=2) + Usage(input=3, cache_read=4) == \
            Usage(input=4, cache_read=4, output=2)

    def test_it_is_frozen_so_a_total_cannot_be_mutated_in_place(self):
        with pytest.raises(Exception):
            Usage().input = 5  # type: ignore[misc]

    def test_as_dict_is_the_shape_a_ledger_row_stores(self):
        assert Usage(input=1, cache_read=2, cache_creation=3, output=4).as_dict() == \
            {"input": 1, "cache_read": 2, "cache_creation": 3, "output": 4}


def test_the_module_imports_nothing_from_services():
    """Pure stdlib on purpose: it must be importable from the FastAPI app, from
    voice_worker, and from the shell through the usage_ledger CLI shim."""
    src = (Path(__file__).resolve().parents[1] / "services" / "token_usage.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "from services" not in code and "import services" not in code
