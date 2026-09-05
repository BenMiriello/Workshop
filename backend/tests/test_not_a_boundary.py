"""`touches` is a real mechanism and it is not a boundary.

The operator's rule: nothing may be represented as a security boundary if it is
not one. `touches` was, in module docstrings, in the sweep's own prompt, and in
the UI copy. This file pins the corrected descriptions so they cannot drift back,
and pins the behavior that IS real so nobody "fixes" the wording by deleting the
mechanism.

What it actually is: an AFTER-THE-FACT AUDIT. `project_ralph.sh` calls
`tasks.enforce_touches` once the run is over, against the committed diff. The
write already happened. What the audit does is name the stray and block the
ticket. Its default is empty, and empty means unrestricted.

THE OTHER HALF OF THIS FILE IS NOW A REMOVAL TEST. A second mechanism, a
per-ticket and per-scope decision-rights dial, used to be pinned here on the same
grounds -- described as prompt text so it could not be mistaken for a permission.
It was deleted on 2026-09-05 (ticket 3b1c5432, ordered
docs/platform-redesign/work-status-contract-2026-08-24.md:159) because it was set
on 0 of 207 tickets and null on every scope, so no run was ever governed by it.
`TestTheDialIsGone` below replaces the wording pins with the stronger claim: a
concept that does not exist cannot be mistaken for a security boundary. That is
also the resolution of ticket 77b22987 [SEC-A2] for this mechanism -- documenting
that it is not confinement and deleting it are incompatible, and deletion wins.

Real confinement is SEC-A1 (ticket 4a79ffc5): bwrap plus a lower-privilege
sub-user. Nothing in this file is a substitute for it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import agent_authority as aa  # noqa: E402
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


class TestTheDialIsGone:
    """The removal, pinned. See the module docstring for why deleting beats
    documenting."""

    _TERM = "latitude"

    def test_no_platform_source_file_still_carries_the_concept(self):
        """The ticket's own acceptance criterion, run as a test. Migrations are
        excluded because the one that drops the column has to name what it
        drops, and the generated client is excluded because it is regenerated
        from the backend rather than edited."""
        roots = ("backend/services", "backend/routes", "backend/db",
                 "scripts", "frontend/src")
        suffixes = (".py", ".sh", ".ts", ".tsx")
        hits = []
        for root in roots:
            for path in sorted((REPO / root).rglob("*")):
                if path.suffix not in suffixes or not path.is_file():
                    continue
                rel = path.relative_to(REPO).as_posix()
                if "migrations" in rel or "schema.gen" in rel:
                    continue
                for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if self._TERM in line.lower():
                        hits.append(f"{rel}:{n}")
        assert hits == [], hits

    def test_the_service_module_is_gone(self):
        assert not (REPO / "backend" / "services" / "latitude.py").exists()

    def test_the_column_is_not_in_the_model(self):
        from db import models
        assert "latitude" not in models.Ticket.__table__.columns
        names = {c.name for c in models.Ticket.__table__.constraints}
        assert "ck_ticket_latitude" not in names

    def test_the_store_does_not_round_trip_it(self, tmp_path):
        """A hand-edited record carrying the old key must not come back out of
        `_normalize` as a field, which is what would let it quietly return."""
        t = tasks.add(tmp_path, title="A ticket", body="With a stale key.")
        assert "latitude" not in tasks.get_task(tmp_path, t["id"])


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
