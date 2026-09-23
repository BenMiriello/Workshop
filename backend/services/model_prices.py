"""What a model call costs in dollars, per model, versioned by the day it ran.

WHY DOLLARS AND NOT A TOKEN UNIT. `token_usage` names four quantities and says
money is not one of them, because money needs the model id. This module is the
missing half. The reasoning is `counting-tokens-2026-08-25.md` section 5 and it
is not a preference: the four components have different prices (output is 5x
input, cache creation 1.25x, cache read 0.1x), so the ratio between "volume
billed" and "dollars" changes with the model, and **no single token total is
even rank-preserving between two scopes running different models**. A budget in
tokens silently changes meaning when the model does. A budget in dollars does
not.

A composite "effective tokens" unit was proposed and withdrawn in that same
section. Do not re-propose it: the field stores raw components and derives money
from a versioned price table (Langfuse, LangSmith, Helicone, Braintrust and
Portkey all do this), the OpenTelemetry GenAI conventions define no weighted
unit, and the one vendor that shipped a weighted composite deprecated it.

VERSIONED BY DATE, NOT BY AN OPAQUE INTEGER. The question asked of this table is
always "what did this cost on the day it ran", so a version IS a date. A ledger
row records the `price_version` in force when it was written; a price change
adds a table and never rewrites history, and an old row keeps the cost it
actually had.

UNPRICED IS A REAL ANSWER AND IT IS NOT ZERO. `cost_usd` returns None for a
model this table does not carry, and every caller must render that as "unpriced"
rather than as $0.00. A guessed price is worse than a gap: it is a number
somebody will act on. When a new model ships, add it here with a source; until
then its spend is visible in tokens and absent in dollars, which is the honest
shape.

Pure stdlib, no `services.*` imports at module scope, for the same reason
`token_usage` has none: this is importable from the FastAPI app, from
voice_worker, and from shell through the usage_ledger CLI shim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CURRENT_VERSION", "CACHE_WRITE_MULTIPLIER", "CACHE_READ_MULTIPLIER",
    "Price", "normalize_model", "price_for", "cost_usd", "known_models",
    "versions",
]

# The two cache multipliers are properties of the API's pricing, not of a model:
# a cache write is billed above fresh input because the prefix has to be stored,
# and a cache read far below it. Published with the prompt-caching docs and
# stable across every model in the tables below, so they are stated once here
# rather than repeated in nine rows.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# A one-HOUR cache write costs more than the five-minute default, because the
# prefix is held longer. NOT a guess: derived by measurement on 2026-09-04 from
# a turn whose published `costUSD` this table missed by $0.5599875 out of
# $81.3843835. Dividing that residual by the per-token gap between a 1.25x and a
# 2.0x write on Opus 5 gives 149,330.00000 tokens -- an exact integer, and 6.96%
# of that turn's 2,146,260 cache-creation tokens. A wrong multiplier does not
# produce an integer.
CACHE_WRITE_1H_MULTIPLIER = 2.00


@dataclass(frozen=True)
class Price:
    """Dollars per MILLION tokens for one model, in one price version.

    Only input and output are stored. The two cache legs are derived from them
    by the published multipliers above, because that is how they are published:
    a table that restated cache prices per model would be four numbers where the
    vendor gives two, and the two extra would drift.
    """

    input_per_mtok: float
    output_per_mtok: float

    @property
    def cache_write_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_WRITE_MULTIPLIER

    @property
    def cache_write_1h_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_WRITE_1H_MULTIPLIER

    @property
    def cache_read_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_READ_MULTIPLIER


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------
#
# SOURCE, and it is the only one this file is allowed to have: the Anthropic
# first-party API price list, as carried by the `claude-api` skill's current
# model table (cached 2026-06-24) and read on 2026-09-04. Anthropic first-party
# rates; Bedrock and Vertex are partner-operated with separate pricing and are
# NOT represented here, so a call routed through either is priced wrong by this
# table rather than not at all. We do not route through either.
#
# NOT PRICED HERE, deliberately, each because the price is conditional rather
# than unknown:
#   - Opus 5 FAST MODE bills at $10/$50 instead of $5/$25. The ledger row does
#     not record `speed`, so a fast-mode call is priced as a standard one and is
#     UNDER-reported. Recording `speed` on the row is what would fix it.
#   - Batch API work bills at 50%. Nothing here submits batches.
#   - The Sonnet 5 introductory rate ($2/$10) expired 2026-08-31, which is
#     before the first version below takes effect, so it never applies to a row
#     this table prices.

_V_2026_09_04 = {
    # Claude 5 family.
    "claude-fable-5": Price(10.0, 50.0),
    "claude-mythos-5": Price(10.0, 50.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-sonnet-5": Price(3.0, 15.0),
    # Still reachable: a pinned ANTHROPIC_MODEL, a resumed session, a delegate.
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}

# Source: platform.claude.com/docs/en/models/overview, read 2026-09-22. Adds
# the two models that shipped after the 09-04 table: Opus 5.5 and Fable 5.1.
# Also carries a real price change the live page shows for Sonnet 5 ($2/$10,
# not the $3/$15 above) -- a NEW version rather than an edit to 09-04, so a row
# billed under the old rate keeps the cost it actually had.
_V_2026_09_22 = {
    **_V_2026_09_04,
    "claude-opus-5-5": Price(4.0, 20.0),
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-sonnet-5": Price(2.0, 10.0),
}

# Keyed by the date the version took effect, newest LAST. `price_for` picks the
# newest version whose date is <= the row's own version, so adding a table here
# never changes what an already-written row costs.
_TABLES: dict[str, dict[str, Price]] = {
    "2026-09-04": _V_2026_09_04,
    "2026-09-22": _V_2026_09_22,
}

CURRENT_VERSION = max(_TABLES)

# A dated snapshot id ("claude-haiku-4-5-20251001") prices as its family. The
# suffix names a snapshot, not a different product, and the published price list
# is keyed by the bare id. Stripping it here means one alias rule instead of a
# row per snapshot date, and an unknown-but-dated model still resolves.
_DATE_SUFFIX = re.compile(r"-(\d{8})$")

# Vendor prefixes a routed call can arrive with. Bedrock ids carry "anthropic.";
# Vertex uses "<id>@<version>". Normalized so the id matches the table, with the
# caveat above that those platforms' PRICES differ and this table does not carry
# them.
_VENDOR_PREFIXES = ("anthropic.", "us.anthropic.", "eu.anthropic.")


def normalize_model(model: str | None) -> str:
    """A model id as the price tables key them: lowercased, unprefixed, undated.

    Tolerant on purpose. This runs on a string that came out of a JSON envelope
    written by another process; a surprising shape must yield "no price", never
    an exception, for the same reason `token_usage.parse` clamps instead of
    raising. Metering is not allowed to cost a turn.
    """
    if not isinstance(model, str):
        return ""
    m = model.strip().lower()
    if not m:
        return ""
    for p in _VENDOR_PREFIXES:
        if m.startswith(p):
            m = m[len(p):]
            break
    m = m.split("@", 1)[0]
    return _DATE_SUFFIX.sub("", m)


def versions() -> list[str]:
    """Every price version, oldest first. A version is the date it took effect."""
    return sorted(_TABLES)


def _table_for(price_version: str | None) -> dict[str, Price]:
    """The table in force at `price_version` (an ISO date), newest that is not
    after it. An unknown or missing version reads as "now", which is the only
    safe default for a row written before versions were recorded."""
    if not price_version:
        return _TABLES[CURRENT_VERSION]
    candidates = [v for v in sorted(_TABLES) if v <= str(price_version)]
    if not candidates:
        # A version OLDER than every table we have. We do not know what it cost
        # and will not pretend: the oldest table is the closest true thing, and
        # it is named in the row, so the gap is visible rather than silent.
        return _TABLES[min(_TABLES)]
    return _TABLES[candidates[-1]]


def known_models(price_version: str | None = None) -> list[str]:
    """Model ids this table can price, for the version in force."""
    return sorted(_table_for(price_version))


def price_for(model: str | None, price_version: str | None = None) -> Price | None:
    """The per-MTok price of `model`, or None if this table does not carry it."""
    return _table_for(price_version).get(normalize_model(model))


def cost_usd(usage: object, model: str | None,
             price_version: str | None = None) -> float | None:
    """Dollars for one call's (or one turn's) components on `model`.

    `usage` is a `token_usage.Usage`, or anything carrying the same four integer
    attributes; a plain dict of the same keys works too, which is what a stored
    ledger row holds.

    A dict may additionally carry `cache_creation_1h`, the portion of
    `cache_creation` written at the one-hour TTL, which is priced at
    CACHE_WRITE_1H_MULTIPLIER instead of the five-minute rate. **The CLI's
    `result` envelope does NOT split it** — `modelUsage` reports one
    `cacheCreationInputTokens` figure — so a row built from a result envelope
    prices every cache write at the 5m rate and UNDER-reports a 1h-heavy turn.
    Measured on the one turn where the split mattered: $80.82 against a
    published $81.38, an 0.69% undercount. The per-message `usage` block DOES
    split it (`cache_creation.ephemeral_1h_input_tokens`), so a writer that has
    the frames can pass it and be exact.

    Returns None — NOT 0.0 — when the model is unknown or unnamed. Those are
    different facts and the ledger reports them differently: a priced row shows
    dollars, an unpriced row shows tokens and says the model was not in the
    table. Summing None as zero is how an unpriced model becomes a free one.
    """
    price = price_for(model, price_version)
    if price is None:
        return None
    if isinstance(usage, dict):
        get = usage.get
    else:
        def get(k: str, default: int = 0) -> object:
            return getattr(usage, k, default)

    def n(key: str) -> int:
        try:
            v = int(get(key, 0) or 0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        return v if v > 0 else 0

    # The 1h portion is a SUBSET of cache_creation, not an addition to it, so it
    # is repriced rather than added. Clamped to the total: a nonsense split must
    # not be able to charge for more cache writes than the turn made.
    write_total = n("cache_creation")
    write_1h = min(n("cache_creation_1h"), write_total)
    return (
        n("input") * price.input_per_mtok
        + (write_total - write_1h) * price.cache_write_per_mtok
        + write_1h * price.cache_write_1h_per_mtok
        + n("cache_read") * price.cache_read_per_mtok
        + n("output") * price.output_per_mtok
    ) / 1_000_000.0


def fmt_usd(v: float | None) -> str:
    """A dollar figure the way a person reads it, and "unpriced" for None.

    Small enough amounts get more decimals rather than rounding to $0.00: a
    single cheap turn costing $0.0043 is a real number, and printing it as
    $0.00 is what makes an operator stop believing the column.
    """
    if v is None:
        return "unpriced"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "unpriced"
    a = abs(f)
    if a >= 1:
        return f"${f:,.2f}"
    if a >= 0.01:
        return f"${f:.3f}"
    if a == 0:
        return "$0.00"
    return f"${f:.5f}"
