"""Two gaps an audit of developer mode found after ADR 0014 shipped.

`test_dev_mode_gate.py` is the per-door acceptance suite. This file covers the
two things that suite could not see, because neither is a request the backend
serves.

1. DOOR 7 AND THE FLAG THAT NEVER REACHED CRON. `dispatch_triggers.sh` iterates
   every project registry row, including the `self: true` one whose dir is the
   repo root, and had no dev-mode branch: the door was closed only because that
   row's stored autonomy mode happens to be `manual`. Worse, WORKSHOP_DEV_MODE
   lives in ~/.config/workshop/instance.env and reaches the API through a systemd
   `EnvironmentFile=`, which cron does not have. So the flag read ON for the
   backend and OFF for everything cron started. The fix is one branch plus one
   `source` line per cron entrypoint, and both are the kind of line a later edit
   drops silently, so they are asserted here.

2. AN UNREACHABLE NOTES LAYER. `knowledge.scope_dir` has mapped `development` to
   shared/knowledge/development since phase 2, but scope enumeration listed only
   directories that already existed, that one never had, and the Notes page
   builds its filter and its New note dialog from the enumeration. The scope was
   addressable by anything that could already name it and by nothing else, which
   meant no UI path to write the first note.

Same framing as the rest: a product capability boundary and a safety interlock,
never a security boundary.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import notes_all  # noqa: E402
from services import knowledge  # noqa: E402
from wsplatform import dev_mode  # noqa: E402

REPO = dev_mode.WORKSHOP_ROOT
SCRIPTS = REPO / "scripts"

# Every entrypoint cron starts that can reach a run target. Each must load the
# deployment config itself; systemd loads it for the units, cron loads nothing.
CRON_ENTRYPOINTS = [
    REPO / "cron_agent.sh",
    SCRIPTS / "dispatch_triggers.sh",
    SCRIPTS / "queue_runner_watchdog.sh",
]


@pytest.fixture(autouse=True)
def dev_mode_off(monkeypatch):
    monkeypatch.delenv(dev_mode.DEV_MODE_ENV, raising=False)
    assert not dev_mode.dev_mode_on()


@pytest.fixture
def dev_mode_on(monkeypatch):
    monkeypatch.setenv(dev_mode.DEV_MODE_ENV, "1")
    assert dev_mode.dev_mode_on()


class TestTheFlagReachesCron:
    @pytest.mark.parametrize("script", CRON_ENTRYPOINTS, ids=lambda p: p.name)
    def test_loads_the_deployment_config(self, script: Path):
        """Without this line the two halves disagree: dev mode on for the API,
        off for anything cron starts. A flag with two answers is not a gate."""
        assert script.is_file(), f"{script} is missing"
        text = script.read_text(encoding="utf-8")
        assert "scripts/lib/instance.sh" in text, (
            f"{script.name} does not source scripts/lib/instance.sh, so "
            f"WORKSHOP_DEV_MODE is unset for everything it starts"
        )

    def test_the_loader_only_fills_what_is_unset(self):
        """An explicit value on the command line must still win, or a one-off
        run cannot override the deployment."""
        text = (SCRIPTS / "lib" / "instance.sh").read_text(encoding="utf-8")
        assert 'if [ -z "${!_k:-}" ]; then export' in text

    def test_the_flag_is_in_the_file_the_loader_reads(self):
        """Not a claim about the value, only that instance.env is where the flag
        is configured. Skipped on an install that has not set one."""
        env = Path.home() / ".config" / "workshop" / "instance.env"
        if not env.is_file():
            pytest.skip("no instance.env on this install")
        assert "WORKSHOP_DEV_MODE" in env.read_text(encoding="utf-8")


class TestDoorSevenIsGated:
    """The dispatcher's project loop. It is bash wrapping Python, so the branch
    is asserted by text, and the PREDICATE it uses is exercised for real below."""

    def test_the_loop_has_a_dev_mode_branch(self):
        text = (SCRIPTS / "dispatch_triggers.sh").read_text(encoding="utf-8")
        assert "skip-dev-mode" in text, "the project loop has no dev-mode branch"
        assert "dev_mode.is_self_dir" in text, (
            "the branch must gate the resolved directory, not the slug: the "
            "platform repo is reachable under at least five names"
        )

    def test_the_branch_runs_before_the_autonomy_preset_test(self):
        """Order matters. The autonomy preset is stored data the operator can
        change from the UI, and it was the only thing closing this door."""
        text = (SCRIPTS / "dispatch_triggers.sh").read_text(encoding="utf-8")
        gate = text.index("skip-dev-mode")
        preset_test = text.index('if preset in ("off", "on-demand"):')
        assert gate < preset_test

    def test_the_predicate_partitions_the_real_registry(self, monkeypatch):
        """The exact condition the loop applies, replayed over the real registry
        in both modes. Off: the self row is the only one skipped, and it is
        skipped whatever its stored autonomy says. On: nothing is skipped."""
        import json

        registry = Path(notes_all._PROJECTS_REGISTRY)
        if not registry.is_file():
            pytest.skip("no projects registry on this install")
        rows = json.loads(registry.read_text(encoding="utf-8")).get("projects") or {}

        def skipped() -> list[str]:
            dev = dev_mode.dev_mode_on()
            return sorted(
                slug for slug, p in rows.items()
                if not dev and (p.get("self")
                                or (p.get("dir") and dev_mode.is_self_dir(p["dir"])))
            )

        selves = sorted(
            slug for slug, p in rows.items()
            if p.get("self") or (p.get("dir") and dev_mode.is_self_dir(p["dir"]))
        )
        assert selves, "no self row in the registry; door 7 has nothing to gate"
        assert skipped() == selves

        monkeypatch.setenv(dev_mode.DEV_MODE_ENV, "1")
        assert skipped() == []

    def test_the_agent_launch_asserts_its_run_target(self):
        """An agent dispatch names a directory, so the shell gate applies at the
        point the sweep is launched (door 5)."""
        text = (SCRIPTS / "dispatch_triggers.sh").read_text(encoding="utf-8")
        assert "dev_mode_gate.sh" in text
        assert "assert_run_target" in text


class TestDevelopmentNotesScope:
    def _dev_dir(self) -> Path:
        return knowledge._shared_knowledge_root() / "development"

    def test_absent_from_enumeration_in_a_normal_install(self):
        assert "development" not in [k for k, _ in notes_all._scope_roots()]

    def test_present_in_developer_mode(self, dev_mode_on):
        keys = [k for k, _ in notes_all._scope_roots()]
        assert "development" in keys
        assert "workshop" in keys

    def test_enumeration_creates_no_directory(self, dev_mode_on):
        """The scope exists because the flag is on, not because a directory is
        on disk. Listing must not be what brings it into being."""
        d = self._dev_dir()
        existed = d.exists()
        notes_all._scope_roots()
        assert d.exists() == existed

    def test_the_write_path_refuses_it_in_a_normal_install(self):
        """`scope_dir` mkdirs, so the refusal has to come before that or a
        normal install grows the scope by being asked about it once."""
        d = self._dev_dir()
        existed = d.exists()
        with pytest.raises(ValueError) as e:
            knowledge.scope_dir("development")
        assert "developer mode is off" in str(e.value)
        assert d.exists() == existed

    def test_the_first_note_can_be_written_and_read_back(self, dev_mode_on):
        d = self._dev_dir()
        existed = d.exists()
        note_id = "dev-scope-reachability-check"

        async def go():
            await knowledge.put_note(
                "development", note_id,
                title="Reachability check",
                hook="written by test_dev_mode_cron_and_notes",
                content="body\n",
            )
            listed = knowledge.list_notes("development")
            agg = await notes_all.list_all(scope="development")
            await knowledge.delete_note("development", note_id)
            return listed, agg

        try:
            listed, agg = asyncio.run(go())
        finally:
            if not existed and d.exists():
                shutil.rmtree(d)

        assert note_id in [n["id"] for n in listed]
        assert note_id in [n["id"] for n in agg["notes"]]
        # `scope=development` filters to development AND EVERYTHING IT REACHES
        # (scope_tree.reach, memory-and-documentation-model-2026-08-14.md sec
        # 4.2), not an exact match -- development is the root in dev mode and
        # reaches every other scope, so `agg["notes"]` legitimately carries
        # non-development rows too. What this test can still pin down without
        # depending on whatever else this box's real knowledge store holds is
        # that the note just written is reachable and tagged with its own
        # scope correctly.
        written = next(n for n in agg["notes"] if n["id"] == note_id)
        assert written["scope"] == "development"
