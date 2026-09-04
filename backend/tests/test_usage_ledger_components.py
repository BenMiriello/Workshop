"""The ledger row stops being one integer.

Three things are asserted here and each closes a measured defect:

1. A v2 row stores the four raw components, so it can be re-evaluated under a
   different weight or a different price table. A v1 row cannot, and there is no
   arithmetic that recovers components from a sum — which is why 6.5 billion
   tokens of history are unrecoverable.
2. Units are never silently added together. A pre-fix chat row holds a sum over
   stream FRAMES; a worker row holds a cache-weighted count. They differ by
   roughly an order of magnitude on the same work and they were being summed.
3. `remaining_for_scope` answers "how much is left", which `can_admit` cannot,
   and it subtracts what a live run has spent and not yet billed. A query that
   skips that is stale by the entire run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import usage_ledger as ul  # noqa: E402


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "LEDGER_PATH", tmp_path / "usage.jsonl")
    monkeypatch.setattr(ul, "BUDGET_CONFIG_PATH", tmp_path / "usage-budget.json")
    return ul.LEDGER_PATH


def rows(ledger):
    return [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]


OPUS = {"input": 2579, "cache_read": 15903, "cache_creation": 437351,
        "output": 10297}


class TestTheRowCarriesComponents:
    def test_a_component_row_is_v2_and_priced(self, ledger):
        ul.record("wstest-a", 451_817, role="worker", components=OPUS,
                  model="claude-opus-5", calls=54, source="result")
        (r,) = rows(ledger)
        assert r["v"] == 2
        assert r["components"] == OPUS
        assert r["unit"] == ul.UNIT_EFFECTIVE
        assert r["model"] == "claude-opus-5"
        assert r["price_version"] == ul.model_prices.CURRENT_VERSION
        assert r["calls"] == 54
        assert r["source"] == "result"
        assert r["cost_usd"] == pytest.approx(3.01171525, abs=1e-6)

    def test_a_row_without_components_is_still_written_and_reads_as_v1(self, ledger):
        """Backward compatibility is not optional: four writers and a CLI shim
        call the positional form."""
        ul.record("wstest-a", 1234, role="worker")
        (r,) = rows(ledger)
        assert r["tokens"] == 1234
        assert "components" not in r
        assert "v" not in r  # written as v1; the VERSION is added on READ
        (loaded,) = ul._read_entries()
        assert loaded["v"] == 1
        assert loaded["unit"] == ul.UNIT_EFFECTIVE  # worker rows were weighted

    def test_the_positional_signature_is_unchanged(self, ledger):
        ul.record("wstest-a", 7)
        assert rows(ledger)[0]["tokens"] == 7

    def test_zero_components_do_not_fake_a_v2_row(self, ledger):
        """An all-zero usage block is not a measurement. Recording it as v2 with
        empty components would claim the turn cost nothing rather than that it
        was never measured — which is exactly the failure the result envelope
        has today."""
        ul.record("wstest-a", 100, role="worker",
                  components={"input": 0, "cache_read": 0,
                              "cache_creation": 0, "output": 0})
        (r,) = rows(ledger)
        assert "components" not in r and "v" not in r

    def test_per_model_components_price_each_leg_on_its_own_sheet(self, ledger):
        """A turn that delegates to a cheaper model spends on two price sheets.
        One `model` label on a summed row prices the whole thing as whichever
        model happened to be larger."""
        models = {
            "claude-opus-5": {"input": 1_000_000, "cache_read": 0,
                              "cache_creation": 0, "output": 0},
            "claude-haiku-4-5-20251001": {"input": 1_000_000, "cache_read": 0,
                                          "cache_creation": 0, "output": 0},
        }
        ul.record("wstest-a", 2_000_000, role="worker",
                  components={"input": 2_000_000, "cache_read": 0,
                              "cache_creation": 0, "output": 0},
                  model="claude-opus-5", models=models)
        (r,) = rows(ledger)
        # $5 for the opus million + $1 for the haiku million. Priced as all-opus
        # it would be $10 — double.
        assert r["cost_usd"] == pytest.approx(6.0)
        assert ul.row_cost_usd(r) == pytest.approx(6.0)

    def test_the_vendors_own_figure_is_kept_beside_ours(self, ledger):
        ul.record("wstest-a", 451_817, role="worker", components=OPUS,
                  model="claude-opus-5", vendor_cost_usd=3.01171525)
        (r,) = rows(ledger)
        assert r["vendor_cost_usd"] == pytest.approx(3.01171525)
        assert r["cost_usd"] == pytest.approx(r["vendor_cost_usd"], abs=1e-6)


class TestUnitsAreNotSilentlyAdded:
    def test_a_v1_chat_row_is_named_raw_frames_and_not_rescaled(self, ledger):
        ul.record("wstest-a", 6_500_000, role="chat", chat="t1")
        (loaded,) = ul._read_entries()
        assert loaded["unit"] == ul.UNIT_RAW_FRAMES
        assert loaded["tokens"] == 6_500_000  # NOT divided by a measured factor

    def test_window_usage_refuses_to_convert_what_it_cannot(self, ledger):
        ul.record("wstest-a", 6_500_000, role="chat", chat="t1")
        ul.record("wstest-a", 100_000, role="worker", components=OPUS,
                  model="claude-opus-5")
        loose = ul.window_usage()  # verbatim: the historical behaviour
        assert loose["app"] == 6_600_000
        strict = ul.window_usage(unit=ul.UNIT_EFFECTIVE)
        # Only the v2 row is expressible, and it is re-derived from components
        # rather than trusted: 2579 + int(0.1*15903) + 437351 + 10297.
        assert strict["app"] == 2579 + 1590 + 437351 + 10297
        assert strict["unconvertible"] == 6_500_000
        assert strict["unconvertible_rows"] == 1

    def test_a_v2_row_can_be_re_expressed_in_a_unit_it_was_not_written_in(self, ledger):
        """The whole point of storing components. `spend` weights cache reads at
        parity, `effective` at 0.1; the same stored row answers both."""
        ul.record("wstest-a", 0, role="worker", components=OPUS,
                  model="claude-opus-5")
        (r,) = ul.window_rows()
        assert ul.row_tokens(r, ul.UNIT_SPEND) == sum(OPUS.values())
        assert ul.row_tokens(r, ul.UNIT_EFFECTIVE) == 2579 + 1590 + 437351 + 10297


class TestMoney:
    def test_the_window_reports_dollars_and_names_what_it_could_not_price(self, ledger):
        ul.record("wstest-a", 451_817, role="worker", components=OPUS,
                  model="claude-opus-5")
        ul.record("wstest-a", 900, role="worker")           # v1, unpriceable
        ul.record("wstest-a", 900, role="worker", components=OPUS,
                  model="claude-not-a-model")              # unknown model
        spend = ul.window_spend_usd()
        assert spend["app_usd"] == pytest.approx(3.01171525, abs=1e-6)
        assert spend["priced_rows"] == 1
        assert spend["unpriced_rows"] == 2
        assert spend["unpriced_tokens"] == 1800
        assert spend["price_version"] == ul.model_prices.CURRENT_VERSION

    def test_an_unpriced_row_is_never_counted_as_free(self, ledger):
        ul.record("wstest-a", 10_000_000, role="worker")
        spend = ul.window_spend_usd()
        assert spend["app_usd"] == 0.0
        # ...but the tokens are reported, so nobody reads $0.00 as "no spend".
        assert spend["unpriced_tokens"] == 10_000_000

    def test_status_carries_the_dollar_block(self, ledger):
        ul.record("wstest-a", 451_817, role="worker", components=OPUS,
                  model="claude-opus-5")
        st = ul.status()
        assert st["usd"]["app_usd"] == pytest.approx(3.01171525, abs=1e-6)

    def test_history_keeps_its_own_price_version(self, ledger):
        """A price table added later must not rewrite what an old row cost."""
        ul.record("wstest-a", 1, role="worker", components=OPUS,
                  model="claude-opus-5")
        (r,) = rows(ledger)
        r["price_version"] = "1999-01-01"
        assert ul.row_cost_usd(r) is not None


class TestRemainingForScope:
    def test_it_answers_a_quantity_where_can_admit_answers_a_verdict(self, ledger):
        ul.BUDGET_CONFIG_PATH.write_text(json.dumps({
            "app_total": 1_000_000, "autonomy_budget_fraction": 1.0,
            "nominal_task_tokens": 1000,
            "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
            "projects": {}}))
        ul.record("wstest-a", 400_000, role="worker")
        left = ul.remaining_for_scope("wstest-a")
        assert left["remaining"] == 600_000
        ok, _ = ul.can_admit("wstest-a")
        assert ok is True  # the verdict agrees; it just cannot say how much

    def test_spent_since_is_subtracted_because_a_run_bills_once_at_exit(self, ledger):
        """CORRECTION 3 of ticket 70860d58. Mid-run the ledger has never heard
        of this run, so a bare query is stale by the whole run so far."""
        ul.BUDGET_CONFIG_PATH.write_text(json.dumps({
            "app_total": 1_000_000, "autonomy_budget_fraction": 1.0,
            "nominal_task_tokens": 1000,
            "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
            "projects": {}}))
        ul.record("wstest-a", 400_000, role="worker")
        assert ul.remaining_for_scope("wstest-a")["remaining"] == 600_000
        mid = ul.remaining_for_scope("wstest-a", spent_since=550_000)
        assert mid["remaining"] == 50_000
        assert mid["spent_since"] == 550_000

    def test_it_never_goes_negative(self, ledger):
        ul.BUDGET_CONFIG_PATH.write_text(json.dumps({
            "app_total": 1_000, "autonomy_budget_fraction": 1.0,
            "nominal_task_tokens": 10,
            "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
            "projects": {}}))
        ul.record("wstest-a", 5_000, role="worker")
        assert ul.remaining_for_scope("wstest-a")["remaining"] == 0

    def test_an_uncapped_config_says_so_rather_than_returning_zero(self, ledger):
        ul.BUDGET_CONFIG_PATH.write_text(json.dumps({"app_total": 0}))
        left = ul.remaining_for_scope("wstest-a")
        assert left["uncapped"] is True and left["remaining"] is None

    def test_chat_spend_does_not_reduce_the_autonomous_remainder(self, ledger):
        """Same rule as admission, and for the same measured reason: the
        operator's own conversation must never be able to starve the loop."""
        ul.BUDGET_CONFIG_PATH.write_text(json.dumps({
            "app_total": 1_000_000, "autonomy_budget_fraction": 1.0,
            "nominal_task_tokens": 1000,
            "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
            "projects": {}}))
        ul.record("wstest-a", 900_000, role="chat", chat="t1")
        assert ul.remaining_for_scope("wstest-a")["remaining"] == 1_000_000


class TestReadingAResultEnvelope:
    def test_model_usage_wins_over_an_all_zero_usage_block(self):
        """MEASURED 2026-09-04 and the reason this code exists: four of four
        surviving `result` envelopes carry `usage` with every count at zero
        while `modelUsage` beside it carries the real numbers. Anything reading
        `usage` bills nothing."""
        envelope = {
            "type": "result",
            "usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "output_tokens": 0},
            "modelUsage": {"claude-opus-5": {
                "inputTokens": 2579, "outputTokens": 10297,
                "cacheReadInputTokens": 15903,
                "cacheCreationInputTokens": 437351,
                "costUSD": 3.01171525}},
            "total_cost_usd": 3.01171525,
        }
        got = ul.usage_from_envelope(envelope)
        assert got["components"] == OPUS
        assert got["model"] == "claude-opus-5"
        assert got["source"] == "result"
        assert got["vendor_cost_usd"] == pytest.approx(3.01171525)

    def test_it_falls_back_to_usage_when_there_is_no_model_usage(self):
        frame = {"type": "assistant",
                 "message": {"model": "claude-sonnet-5",
                             "usage": {"input_tokens": 5, "output_tokens": 7}},
                 "usage": {"input_tokens": 5, "output_tokens": 7}}
        got = ul.usage_from_envelope(frame)
        assert got["components"]["input"] == 5
        assert got["model"] == "claude-sonnet-5"
        assert got["source"] == "frames"

    def test_the_heaviest_model_names_the_row_but_every_leg_is_priced(self):
        envelope = {"type": "result", "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 10, "outputTokens": 0,
                                          "cacheReadInputTokens": 0,
                                          "cacheCreationInputTokens": 0},
            "claude-opus-5": {"inputTokens": 1000, "outputTokens": 0,
                              "cacheReadInputTokens": 0,
                              "cacheCreationInputTokens": 0}}}
        got = ul.usage_from_envelope(envelope)
        assert got["model"] == "claude-opus-5"
        assert set(got["models"]) == {"claude-opus-5", "claude-haiku-4-5-20251001"}

    def test_junk_yields_no_measurement_rather_than_an_exception(self):
        for junk in ({}, {"usage": "nope"}, {"modelUsage": []},
                     {"modelUsage": {"m": "nope"}}):
            got = ul.usage_from_envelope(junk)
            assert got["components"] is None
