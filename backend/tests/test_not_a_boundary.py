"""`touches` and `latitude` are real mechanisms, and neither is a boundary.

The operator's rule: nothing may be represented as a security boundary if it is
not one. These two were, in module docstrings, in the sweep's own prompt, and in
the UI copy. This file pins the corrected descriptions so they cannot drift back,
and pins the behavior that IS real so nobody "fixes" the wording by deleting the
mechanism.

What each one actually is:

  touches    An AFTER-THE-FACT AUDIT. `project_ralph.sh` calls
             `tasks.enforce_touches` once the run is over, against the committed
             diff. The write already happened. What the audit does is name the
             stray and block the ticket.
  latitude   PROMPT TEXT, with exactly one machine-visible consequence: a
             `strict` run whose completion gate came back ambiguous blocks for
             the operator rather than retrying.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import agent_authority as aa  # noqa: E402
from services import latitude as lat  # noqa: E402
from services import tasks  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _text(rel: str) -> str:
    return (REPO / rel).read_text()


class TestTheAuditStillWorks:
    """Deleting the overclaim must not delete the mechanism."""

    def test_a_stray_path_blocks_the_ticket_with_an_evidence_line(self, tmp_path):
        t = tasks.add(tmp_path, title="Narrow ticket", body="Only the one file.",
                      touches=["backend/services/tasks.py"])
        bad = tasks.enforce_touches(
            tmp_path, t["id"], ["backend/services/tasks.py", "infra/secrets.tf"])
        assert bad == ["infra/secrets.tf"]
        after = tasks.get_task(tmp_path, t["id"])
        assert after["status"] == "blocked"
        assert "infra/secrets.tf" in (after.get("blocked_reason") or "")

    def test_an_in_lane_run_is_left_alone(self, tmp_path):
        t = tasks.add(tmp_path, title="Narrow ticket", body="Only the one file.",
                      touches=["backend/services/*.py"])
        assert tasks.enforce_touches(
            tmp_path, t["id"], ["backend/services/tasks.py"]) == []
        assert tasks.get_task(tmp_path, t["id"])["status"] == "todo"

    def test_no_declared_touches_means_unrestricted(self, tmp_path):
        t = tasks.add(tmp_path, title="Open ticket", body="Anywhere.")
        assert tasks.enforce_touches(tmp_path, t["id"], ["anything/at/all.py"]) == []


class TestTouchesIsDescribedAsAnAudit:
    def test_the_docstring_says_it_runs_after_the_write(self):
        doc = tasks.enforce_touches.__doc__ or ""
        assert "NOT A GATE" in doc
        assert "already been written and committed" in doc

    def test_the_section_header_no_longer_calls_it_a_boundary(self):
        src = _text("backend/services/tasks.py")
        assert "touches: the task's fan-out boundary" not in src
        assert "an after-the-fact audit of what a run wrote" in src


class TestLatitudeIsDescribedAsPromptText:
    def test_the_module_no_longer_claims_an_enforced_floor(self):
        doc = lat.__doc__ or ""
        assert "enforced floor" not in doc
        assert "PROMPT TEXT" in doc

    def test_the_sweep_does_not_tell_the_model_that_touches_binds_it(self):
        """The model can falsify this claim by simply writing the file, and a
        prompt that asserts a falsifiable constraint teaches it to discount the
        rest of the prompt."""
        src = _text("scripts/ralph.sh")
        assert "bind you absolutely" not in src
        assert "enforced floor" not in src
        assert "it is an\nAUDIT, not a gate" in src

    def test_the_ui_copy_does_not_promise_a_floor_either(self):
        src = _text("frontend/src/lib/tasks.ts")
        assert "enforced floor" not in src
        # Was pinned to the literal string "decides freely, reports the decisions
        # at the end". That phrasing was itself the problem: it describes a
        # PRIVILEGE LEVEL, which is the reading this whole file exists to stop.
        # Latitude only shapes whether a worker ASKS when the ticket is silent.
        # So the assertion now pins the property instead of one sentence
        # (autonomy-implementation-2026-08-13 2.3).
        for line in ("Asks when the ticket does not say.",
                     "Decides reversible things, asks about the rest.",
                     "Decides and reports at the end."):
            assert line in src, line

    def test_the_latitude_copy_says_what_the_worker_does_not_what_it_may_do(self):
        """Each description has to be about BEHAVIOUR. A word like "allowed" or
        "restricted" turns a prompt hint back into a permission in the reader's
        head, which is exactly how `touches` came to be trusted as a wall."""
        src = _text("frontend/src/lib/tasks.ts")
        start = src.index("const LATITUDE_DESC")
        block = src[start:src.index("};", start)]
        for word in ("allowed", "permitted", "may not", "cannot", "forbidden",
                     "restricted", "blocked from", "limit"):
            assert word not in block.lower(), f"{word!r} reads as permission"


class TestRoleNarrowingIsDescribedWhereItApplies:
    """Item 18's audit claim was half wrong and the correction belongs in code.

    The claim was that a cron-dispatched keeper or triager "runs with the full
    inherited toolset including Write/Edit/Bash". It does not: both role runners
    call the model with tools switched off entirely, so those two roles run with
    FEWER tools than the role table grants. The real gap was the maker sweep,
    and for a maker an allowlist is meaningless because the role includes Bash.
    """

    def test_the_keeper_and_triager_runners_disable_tools(self):
        for module in ("triager.py", "memory_keeper.py"):
            src = _text(f"backend/services/{module}")
            assert "tools_disabled=True" in src, module

    def test_neither_role_runner_spawns_a_tool_carrying_agent(self):
        for script in ("triager_run.sh", "memory_keeper_run.sh"):
            src = _text(f"scripts/{script}")
            assert "--allowedTools" not in src
            assert "-p " not in src

    def test_a_maker_allowlist_would_contain_bash_and_so_restrict_nothing(self):
        """The reason the maker sweep is NOT given --allowedTools, asserted so
        the reasoning survives the next person who reads the gap as an
        oversight."""
        assert "Bash" in aa.tools_for_role("maker")

    def test_the_module_says_where_the_tool_list_applies(self):
        doc = aa.__doc__ or ""
        assert "WHERE `tools_for_role` ACTUALLY APPLIES" in doc
        assert "tools_disabled=True" in doc

    def test_the_agent_page_scopes_its_tool_claim_to_the_chat(self):
        src = _text("frontend/src/pages/AgentDetail.tsx")
        assert "Tools when addressed in a chat:" in src


class TestLimitsAreNoLongerOffered:
    """REMOVAL test. max_iterations and budget.max_wall_s were editable,
    persisted, and read by nothing: a sweep's iteration cap comes from the
    ticket's effort tier, and `limits.budget` reached only `triggers.dispatch()`,
    which has no callers."""

    def test_the_agent_page_no_longer_edits_them(self):
        """Scoped to the agent's own Limits section. A `loop` TRIGGER carries its
        own max_iterations in the trigger editor; that is a different field on a
        different record and is not what this removed."""
        src = _text("frontend/src/pages/AgentDetail.tsx")
        assert "<SectionLabel>Limits</SectionLabel>" not in src
        assert "ad-iters" not in src
        assert "ad-budget" not in src
        assert "agent.limits" not in src

    def test_it_does_not_send_them_on_save(self):
        src = _text("frontend/src/pages/AgentDetail.tsx")
        assert "limits: dLimits" not in src

    def test_dispatch_never_reads_them(self):
        """If this starts failing, the field became real and the editor can
        come back."""
        src = _text("scripts/dispatch_triggers.sh")
        assert "max_iterations" not in src
        assert "max_wall_s" not in src


@pytest.mark.parametrize("value", list(lat.LATITUDES))
def test_every_latitude_still_resolves(value):
    """The cascade is untouched by the rewording."""
    got, source = lat.resolve(invocation=value)
    assert (got, source) == (value, "invocation")
