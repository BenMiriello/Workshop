"""Every usage writer records components, and none re-derives a token count.

`services/token_usage.py` was written as "the one central seam" and then had
ZERO importers for ten days — BACKLOG-MEASURED-2026-09-04 item 6's first
deciding command was `grep -rn token_usage backend/ | grep -v tests` returning
nothing. A seam nobody calls is a file, not a seam, and the way that happened
again would be a new writer quietly summing a usage dict itself.

So this asserts REACHABILITY, not presence: each of the four writers is exercised
or read for the specific property that it hands components to the ledger rather
than an integer it computed on its own.

The four, measured 2026-09-04:
    build_streaming   role=chat       a typed turn
    voice_worker      role=chat       a spoken turn
    scripts/ralph.sh  role=worker     an autonomous sweep
    scripts/reviewer.sh role=reviewer the auxiliary gate

(`routes/chat.py`'s role=compaction writer is gone; the role stays in
ADMISSION_EXCLUDED_ROLES for the 1,813 historical rows that carry it.)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import usage_ledger as ul

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BACKEND = Path(__file__).resolve().parents[1]

# One real result envelope, as the CLI emits it: `usage` all zeros, the truth in
# `modelUsage`. Lifted from .runtime/turns/ on 2026-09-04.
REAL_RESULT = {
    "type": "result",
    "usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "output_tokens": 0},
    "modelUsage": {"claude-opus-5": {
        "inputTokens": 2579, "outputTokens": 10297,
        "cacheReadInputTokens": 15903, "cacheCreationInputTokens": 437351,
        "costUSD": 3.01171525}},
    "total_cost_usd": 3.01171525,
}


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "LEDGER_PATH", tmp_path / "usage.jsonl")
    monkeypatch.setattr(ul, "BUDGET_CONFIG_PATH", tmp_path / "usage-budget.json")
    return ul.LEDGER_PATH


class TestTheSeamIsReached:
    def test_the_seam_has_importers_at_all(self):
        """The literal check that BACKLOG item 6 ran and that returned zero."""
        importers = []
        for p in BACKEND.rglob("*.py"):
            if "tests" in p.parts or p.name == "token_usage.py":
                continue
            if "token_usage" in p.read_text(errors="replace"):
                importers.append(p.name)
        assert importers, "services.token_usage still has no importers"
        assert "usage_ledger.py" in importers
        assert "model_prices.py" in importers

    def test_a_result_envelope_becomes_a_priced_row(self):
        parsed = ul.usage_from_envelope(REAL_RESULT)
        ul.record("wstest-a", 0, role="chat", components=parsed["components"],
                  models=parsed["models"], model=parsed["model"],
                  source=parsed["source"],
                  vendor_cost_usd=parsed["vendor_cost_usd"])
        row = json.loads(ledger_line(ul.LEDGER_PATH))
        assert row["v"] == 2
        assert row["cost_usd"] == pytest.approx(3.01171525, abs=1e-6)
        assert row["source"] == "result"


def ledger_line(path: Path) -> str:
    return [x for x in path.read_text().splitlines() if x.strip()][-1]


class TestNoWriterReDerivesATokenCount:
    """Each writer must hand the seam components, not a number it summed.

    Read as source rather than executed, because three of the four are shell or
    need a live CLI. What is checked is the specific expression that would mean
    the writer went back to doing its own arithmetic."""

    def test_ralph_meters_through_the_cli_shim(self):
        src = (SCRIPTS / "ralph.sh").read_text()
        assert "usage_ledger \\\n" in src or "usage_ledger " in src
        assert "read-usage --usage-json" in src
        assert "--usage-accum" in src
        # The old inline weight is gone from this file.
        assert "0.1 * int(u.get('cache_read_input_tokens'" not in src

    def test_the_reviewer_meters_through_the_cli_shim(self):
        src = (SCRIPTS / "reviewer.sh").read_text()
        assert "record \\\n" in src or "usage_ledger record" in src
        assert "--usage-json" in src
        assert "0.1 * int(u.get('cache_read_input_tokens'" not in src

    def test_the_reviewer_says_so_when_it_meters_nothing(self):
        """It used to compute 0 and write nothing, which is indistinguishable
        from a deterministic review that legitimately spent no tokens."""
        src = (SCRIPTS / "reviewer.sh").read_text()
        assert "reviewer metered NOTHING" in src

    def test_the_chat_path_records_components(self):
        src = (BACKEND / "services" / "build_streaming.py").read_text()
        i = src.index("usage_ledger.record(")
        call = src[i:i + 700]
        for field in ("components=", "models=", "model=", "source=",
                      "vendor_cost_usd="):
            assert field in call, f"chat writer does not pass {field}"

    def test_the_voice_path_records_components(self):
        src = (BACKEND / "services" / "voice_worker.py").read_text()
        i = src.index("usage_ledger.record(")
        call = src[i:i + 700]
        for field in ("components=", "models=", "model=", "source="):
            assert field in call, f"voice writer does not pass {field}"
        # And it is handed the WHOLE envelope, because `usage` on a result is
        # all zeros on this build.
        assert "envelope=ev" in src

    def test_both_chat_writers_use_one_parse(self):
        """Voice and text agreeing BY CONSTRUCTION is the property that was
        missing when voice went unmetered for months."""
        for name in ("build_streaming.py", "voice_worker.py"):
            src = (BACKEND / "services" / name).read_text()
            assert "usage_from_envelope" in src, name


class TestTheUnitIsFinallyOne:
    def test_chat_and_worker_rows_land_in_the_same_unit(self):
        """The defect in one sentence: for months a chat row held a sum over
        stream FRAMES and a worker row held a cache-weighted count, and
        window_usage added them. On the same components they now agree."""
        parsed = ul.usage_from_envelope(REAL_RESULT)
        for role in ("chat", "worker"):
            ul.record("wstest-a", 0, role=role,
                      components=parsed["components"], model=parsed["model"])
        rows = [json.loads(x) for x in ul.LEDGER_PATH.read_text().splitlines() if x]
        units = {r["unit"] for r in rows}
        assert units == {ul.UNIT_EFFECTIVE}
        strict = ul.window_usage(unit=ul.UNIT_EFFECTIVE)
        assert strict["unconvertible_rows"] == 0
