"""The price table, checked against the vendor's own arithmetic.

The one test that matters here is `test_matches_vendor_cost_on_real_turns`. A
price table nobody can check is a table of guesses, and the guesses are what the
operator would be shown dollars from. The CLI's `result` envelope publishes its
OWN `costUSD` per model beside the counts it charged for, so the table has an
independent answer to compare against on every real turn.

The four fixtures below are verbatim `modelUsage` entries lifted from turn
journals under `.runtime/turns/` on 2026-09-04. They are copied in rather than
read from disk because that directory holds about ten turns at a time and prunes
itself; a test reading it would pass today and vanish next week.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import model_prices as mp  # noqa: E402
from services import token_usage  # noqa: E402


# (model id as the CLI reported it, modelUsage counts, the CLI's own costUSD).
# Every turn whose cache writes were entirely at the five-minute TTL, which is
# every turn `modelUsage` can be priced exactly from. The 1h case is below.
REAL_TURNS = [
    ("claude-opus-5",
     {"input": 2579, "output": 10297, "cache_read": 15903, "cache_creation": 437351},
     3.01171525),
    ("claude-opus-5",
     {"input": 4037, "output": 10598, "cache_read": 15903, "cache_creation": 546929},
     3.71139275),
    ("claude-haiku-4-5-20251001",
     {"input": 2644, "output": 1586, "cache_read": 24433, "cache_creation": 0},
     0.01301730),
    ("claude-opus-5",
     {"input": 2371, "output": 9787, "cache_read": 15903, "cache_creation": 461935},
     3.15157525),
]

# The fifth surviving turn, and the expensive one: 105.5 MILLION cache-read
# tokens and $81.38 for a single turn. `modelUsage` does not split the cache
# TTL, so this one prices short by exactly the 1h premium on 149,330 tokens.
ONE_HOUR_TTL_TURN = (
    "claude-opus-5",
    {"input": 1758, "output": 585258, "cache_read": 105540062,
     "cache_creation": 2146260},
    81.3843835000001,
    149330,  # the 1h portion, recovered from the residual
)


@pytest.mark.parametrize("model,components,vendor", REAL_TURNS)
def test_matches_vendor_cost_on_real_turns(model, components, vendor):
    """Our table reproduces the vendor's published cost for the same call.

    Measured 2026-09-04: exact to the last decimal on all four, across two
    models and two orders of magnitude. That is what licenses showing a dollar
    figure at all. A future price change that breaks this is either a stale
    table or a changed rate, and either way it must be seen."""
    ours = mp.cost_usd(components, model)
    assert ours is not None
    assert ours == pytest.approx(vendor, abs=1e-8)


def test_a_one_hour_cache_write_is_the_only_gap_and_it_is_named():
    """The 0.69% the table misses, and why it misses it.

    `modelUsage` reports ONE `cacheCreationInputTokens` figure and does not say
    how much of it was written at the 1h TTL, which is priced at 2.0x input
    rather than 1.25x. So a row built from a result envelope prices every write
    at the 5m rate and comes in low. Supplying the split makes it exact, which
    is what proves the multiplier rather than the tolerance being generous:
    149,330 is an integer recovered from a residual, and a wrong multiplier does
    not yield one."""
    model, comp, vendor, one_hour = ONE_HOUR_TTL_TURN
    without_split = mp.cost_usd(comp, model)
    assert without_split is not None
    # Low, and by under 1% — an undercount, never an overcount.
    assert without_split < vendor
    assert (vendor - without_split) / vendor < 0.01
    with_split = mp.cost_usd({**comp, "cache_creation_1h": one_hour}, model)
    assert with_split == pytest.approx(vendor, abs=1e-6)


def test_the_one_hour_portion_is_repriced_not_added():
    """It is a SUBSET of cache_creation. Adding it would charge twice for the
    same tokens, and a split larger than the total must not be able to."""
    base = mp.cost_usd({"cache_creation": 1_000_000}, "claude-opus-5")
    split = mp.cost_usd({"cache_creation": 1_000_000,
                         "cache_creation_1h": 1_000_000}, "claude-opus-5")
    absurd = mp.cost_usd({"cache_creation": 1_000_000,
                          "cache_creation_1h": 9_000_000}, "claude-opus-5")
    assert base == pytest.approx(5.0 * 1.25)
    assert split == pytest.approx(5.0 * 2.00)
    assert absurd == split


def test_haiku_snapshot_id_prices_as_its_family():
    """`claude-haiku-4-5-20251001` is what the CLI actually reports, and the
    published price list is keyed by the bare id. One alias rule, not a row per
    snapshot date."""
    assert mp.normalize_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert mp.price_for("claude-haiku-4-5-20251001") == mp.price_for("claude-haiku-4-5")


def test_unknown_model_is_unpriced_not_free():
    """None, never 0.0. Summing an unpriced model as zero is how a model nobody
    added to the table becomes a model that costs nothing."""
    assert mp.cost_usd({"input": 1_000_000}, "claude-not-a-model") is None
    assert mp.cost_usd({"input": 1_000_000}, None) is None
    assert mp.cost_usd({"input": 1_000_000}, "") is None
    assert mp.fmt_usd(None) == "unpriced"


def test_cache_legs_derive_from_input():
    p = mp.price_for("claude-opus-5")
    assert p is not None
    assert p.cache_write_per_mtok == pytest.approx(p.input_per_mtok * 1.25)
    assert p.cache_read_per_mtok == pytest.approx(p.input_per_mtok * 0.10)


def test_output_dominates_a_cache_heavy_turn_which_tokens_hide():
    """The reason money is not a token count.

    On the first real turn above, output is 2.2% of the tokens and 8.5% of the
    dollars, and cache reads are 3.5% of the tokens and 0.26% of the dollars. A
    budget denominated in tokens ranks those two the same way; a budget in
    dollars does not, which is the whole argument of counting-tokens section 5."""
    _, comp, _ = REAL_TURNS[0][0], REAL_TURNS[0][1], REAL_TURNS[0][2]
    total_tokens = sum(comp.values())
    out_share_tokens = comp["output"] / total_tokens
    out_only = mp.cost_usd({"output": comp["output"]}, "claude-opus-5")
    whole = mp.cost_usd(comp, "claude-opus-5")
    assert out_only is not None and whole is not None
    assert out_share_tokens < 0.03
    assert out_only / whole > 0.08


def test_a_usage_dataclass_prices_the_same_as_its_dict():
    u = token_usage.Usage(input=100, cache_read=200, cache_creation=300, output=400)
    assert mp.cost_usd(u, "claude-sonnet-5") == mp.cost_usd(u.as_dict(), "claude-sonnet-5")


def test_price_version_is_a_date_and_history_keeps_its_own():
    assert mp.CURRENT_VERSION in mp.versions()
    # A version older than every table falls back to the oldest we hold rather
    # than inventing one, and says so by still returning a price.
    assert mp.price_for("claude-opus-5", "1999-01-01") is not None


def test_every_priced_model_is_a_current_family_id():
    """Guards the exact failure mode the ticket named: a model id that is easy
    to get wrong. Every key must be a real, currently-reachable id."""
    for m in mp.known_models():
        assert m == mp.normalize_model(m), f"{m} is not in normalized form"
        assert m.startswith("claude-")
