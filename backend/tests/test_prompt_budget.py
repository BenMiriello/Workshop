"""Every prefix block declares what bounds it, and the gate can sum it.

THE DEFECT THIS CLOSES is a gap in the contract, not a bug in any block. The
four-layer prompt contract specifies AUTHORITY — who owns L0 to L3, what order
they assemble in, what is confidential — and never assigns a byte budget to a
layer or names an owner for the total. So each block author capped their own
block (five did, unprompted, each with a written rationale beside the constant)
and nobody owned the sum. The one block whose author did not cap it read a
growing store whole, and there was nothing for it to fail against and nothing
above it to notice: a fresh chat opened with a 114,192-char prefix, 68% of it
that block, before the operator had typed anything.

THE REGISTRY'S FIRST ACT WAS TO CONTRADICT ITS OWN DESIGN DOC, which predicted
one uncapped block. There are nine. That is the finding, not a defect in the
registry — it is what a declaration is for, and none of the nine was reachable
by reading any single file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services import chat_context as cc

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "check_prompt_size.py"


class TestTheRegistryDescribesTheRealAssembler:
    def test_every_block_the_assembler_appends_has_a_row(self):
        """A block missing from the registry is invisible to the gate, which is
        this design's one failure mode. Checked against the assembler's actual
        call sites rather than against a list, so adding a block without a row
        fails here."""
        src = (ROOT / "backend" / "routes" / "chat.py").read_text()
        start = src.index("def _build_prompt(")
        body = src[start:src.index("def _warn_if_over_prefix_budget")]
        names = {b.name for b in cc.PROMPT_BLOCKS}
        # The producers `_build_prompt` calls, and the registry row each must have.
        for call, row in (
            ("chat_context.platform_kernel()", "platform_kernel"),
            ("chat_context.runtime_briefing(provider)", "runtime_briefing"),
            ("chat_context.orientation_manifest(provider)", "orientation_manifest"),
            ("provider.persona()", "persona"),
            ("provider.capability_note(", "capability_note"),
            ("chat_context.learnings_context(provider)", "standing_notes"),
            ("chat_context.ambient_digest(provider)", "ambient_digest"),
            ("chat_context.working_state_block(provider)", "working_state"),
            ("knowledge.layered_index_block(", "knowledge_index"),
            ("skills.descriptions_block(", "skills"),
            ("provider.custom_prompt()", "custom_prompt_row"),
            ("provider.live_context()", "live_context"),
        ):
            assert call in body, f"{call} is no longer in _build_prompt"
            if row == "custom_prompt_row":
                assert "custom_instructions" in names
            else:
                assert row in names, f"no PROMPT_BLOCKS row for {call}"

    def test_a_cap_is_a_path_not_a_copied_number(self):
        """Restating a cap is how the registry and the code start disagreeing,
        and a registry that disagrees is worse than none: the gate goes on
        passing while the prompt grows."""
        for b in cc.PROMPT_BLOCKS:
            if b.cap is None:
                continue
            assert "." in b.cap, f"{b.name} cap is not a dotted path: {b.cap!r}"
            assert not b.cap.isdigit()

    def test_every_declared_cap_resolves(self):
        """A renamed constant must be LOUD. `learnings.py` became `standing.py`
        between this being designed and being built, and a copied number would
        have gone on reporting the old value forever."""
        for b in cc.PROMPT_BLOCKS:
            if b.cap is None:
                continue
            assert cc._resolve_dotted(b.cap) is not None, (
                f"{b.name}'s cap {b.cap} does not resolve; the constant was "
                f"renamed or moved and the registry did not follow")

    def test_a_capped_block_names_what_it_drops(self):
        """A cap alone produces silent truncation, which is worse than no cap:
        the agent cannot tell a store that is EMPTY from one that was CUT."""
        for b in cc.PROMPT_BLOCKS:
            if b.cap_chars() is None or b.name == "platform_kernel":
                continue  # fixed text drops nothing
            assert b.drops, f"{b.name} declares a cap and no drop rule"

    def test_a_count_cap_is_not_a_byte_budget(self):
        """Six tickets is six titles or six essays. Reporting a count cap as a
        byte budget is how a block with no bound looks bounded."""
        digest = next(b for b in cc.PROMPT_BLOCKS if b.name == "ambient_digest")
        assert digest.cap_unit == cc.CAP_COUNT
        assert digest.cap_chars() is None
        assert "ambient_digest" in cc.prefix_budget()["uncapped"]

    def test_the_conversation_fuse_is_excluded_from_the_prefix_sum(self):
        """600k tokens against an ~11k prefix budget. Summing it in would swamp
        every other row and make the ceiling meaningless."""
        assert "conversation" in {b.name for b in cc.PROMPT_BLOCKS}
        assert "conversation" in cc.PREFIX_EXCLUDED_BLOCKS
        budget = cc.prefix_budget()
        assert "conversation" not in budget["by_block"]
        assert "conversation" not in budget["uncapped"]


class TestWhatTheRegistryRevealed:
    def test_the_uncapped_count_is_nine_not_one(self):
        """The design predicted one. Pinning the real number is the point: this
        assertion is expected to FALL over time and each fall is a block that
        acquired a byte budget."""
        budget = cc.prefix_budget()
        assert len(budget["uncapped"]) <= 9, (
            "a prefix block lost its byte budget; the registry exists to make "
            "that impossible to do quietly")

    def test_the_operators_own_layer_is_uncapped_on_purpose(self):
        """L3 is the layer the contract protects. Capping the operator to fit
        platform text is the authority inversion the design exists to prevent,
        so if anything is ever trimmed it is not this."""
        row = next(b for b in cc.PROMPT_BLOCKS if b.name == "custom_instructions")
        assert row.cap_chars() is None
        assert "not this" in row.note

    def test_the_original_defect_is_named_in_the_registry(self):
        row = next(b for b in cc.PROMPT_BLOCKS if b.name == "live_context")
        assert row.cap_chars() is None
        assert "Unbounded by construction" in row.note


class TestTheRuntimeHalfLogsAndNeverFails:
    def test_it_warns_over_budget_and_still_returns_the_prompt(self, caplog,
                                                               monkeypatch):
        import logging
        from routes import chat as chat_routes

        class P:
            key = "wstest-scope"
        parts = ["=== BIG ===\n" + "x" * 60_000,
                 "=== NOW REPLY ===\nOperator: hi"]
        with caplog.at_level(logging.WARNING, logger="workshop.chat"):
            chat_routes._warn_if_over_prefix_budget(parts, P())
        assert any("prompt prefix over budget" in r.message for r in caplog.records), \
            caplog.text
        assert "BIG" in caplog.text

    def test_it_is_silent_under_budget(self, caplog):
        import logging
        from routes import chat as chat_routes

        class P:
            key = "wstest-scope"
        with caplog.at_level(logging.WARNING, logger="workshop.chat"):
            chat_routes._warn_if_over_prefix_budget(["=== SMALL ===\nhi"], P())
        assert not [r for r in caplog.records
                    if "prompt prefix over budget" in r.message]

    def test_the_user_turn_and_conversation_are_not_charged_to_the_prefix(self):
        parts = ["=== A ===\n" + "x" * 100,
                 "=== CONVERSATION SO FAR ===\n" + "y" * 100_000,
                 "=== NOW REPLY ===\n" + "z" * 5_000]
        assert cc.prefix_chars(parts) == len(parts[0])

    def test_a_measurement_failure_never_costs_a_turn(self, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(cc, "prefix_chars",
                            lambda p: (_ for _ in ()).throw(RuntimeError("boom")))

        class P:
            key = "wstest-scope"
        chat_routes._warn_if_over_prefix_budget(["x"], P())  # must not raise


class TestTheGate:
    def _run(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(GATE), *args],
                              capture_output=True, text=True, timeout=120,
                              cwd=str(cwd or ROOT))

    def test_it_runs_under_a_bare_interpreter_with_no_backend_deps(self):
        """It reads the registry BY PARSING SOURCE, on purpose. The first
        version imported chat_context, which raised under the reviewer's system
        interpreter (no httpx), fell back to an empty registry, and printed
        `uncapped_blocks: 0` — arming the ceiling against a sum of nothing. A
        gate that reports PASS when it can see nothing is the exact failure it
        exists to catch, reproduced inside itself."""
        r = self._run("--json")
        assert r.returncode in (0, 1), r.stderr
        # --json emits JSON AND NOTHING ELSE. It used to print the payload and
        # then the human report under it, so the first caller to try json.loads
        # failed on "Extra data". A --json mode that is not parseable is a trap.
        payload = json.loads(r.stdout)
        assert payload["_blocks"] == len(cc.PROMPT_BLOCKS) - 1  # conversation excluded
        assert payload["uncapped_blocks"] > 0
        assert payload["declared_budget_chars"] > 0

    def test_it_agrees_with_the_module_it_parses(self):
        """The static read and the runtime read must produce the same numbers,
        or the gate is pinning something other than what runs."""
        r = self._run("--json")
        payload = json.loads(r.stdout)
        live = cc.prefix_budget()
        assert payload["declared_budget_chars"] == live["capped_chars"]
        assert payload["uncapped_blocks"] == len(live["uncapped"])
        assert payload["_capped_by_block"] == live["by_block"]

    def test_it_resolves_the_env_override_idiom_to_its_default(self):
        """Three caps are written `int(os.environ.get("X", "1800"))`. The gate
        must pin the DECLARED default; a number that moved with the shell would
        not be a ratchet."""
        r = self._run("--json")
        payload = json.loads(r.stdout)
        assert payload["_capped_by_block"]["standing_notes"] == 1800 * 4

    def test_it_does_not_offer_delta_mode(self):
        """All three counts are pure functions of tracked source, so there is no
        inherited-debt problem for `--since` to solve. Stated so the next person
        does not add it for symmetry."""
        r = self._run("--since", "HEAD")
        assert r.returncode != 0
        assert "unrecognized arguments" in r.stderr
        assert "DELTA MODE IS NOT OFFERED" in GATE.read_text()

    def test_the_reviewer_runs_it_and_fails_on_it(self):
        src = (ROOT / "scripts" / "reviewer.sh").read_text()
        assert "check_prompt_size.py" in src
        assert 'PROMPT_SIZE_CHECK="fail"' in src
        assert 'REASONS+=("prompt_budget_got_worse")' in src

    def test_the_baseline_is_pinned(self):
        base = json.loads((ROOT / "scripts" / "prompt_size_baseline.json").read_text())
        for k in ("static_chars", "declared_budget_chars", "uncapped_blocks",
                  "ceiling_chars"):
            assert isinstance(base[k], int), k

    def test_the_ceiling_is_not_enforced_while_a_block_is_uncapped(self):
        """Not leniency. An uncapped block contributes 0, so a ceiling checked
        against an incomplete sum reads as passing while the thing it bounds is
        unbounded."""
        r = self._run()
        assert "ceiling NOT ENFORCED" in r.stdout, r.stdout
        assert cc.prefix_budget()["uncapped"], (
            "every block is capped now — arm the ceiling and delete this test")
