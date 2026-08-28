"""Drift: what is running versus what is on disk, and what is holding it stale.

THE FAILURE UNDER TEST IS NOT A CRASH. On 2026-08-27 two uncommitted files sat
in a shared checkout and `scripts/apply.sh` correctly refused to build over
them, for about nine hours, while every agent that finished a UI change believed
it had shipped. Nothing was broken; the refusal was simply printed into one
terminal and read by nobody. So what these tests pin is that the `blocked`
state EXISTS AND IS DISTINGUISHABLE from `stale` -- rerunning the apply step
fixes one and does nothing at all for the other, and a view that conflated them
would send the operator round the same loop that cost the nine hours.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import drift as drift_routes  # noqa: E402
from services import drift  # noqa: E402


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A checkout shaped like this one: the two source-id scripts, a dist stamp.

    The scripts are stubs that echo a fixed id, because what is under test here
    is the COMPARISON and the blocking rule, not the hashing -- `ui_source_id.sh`
    owns that and says so.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "dist").mkdir(parents=True)
    (root / "backend").mkdir(parents=True)
    for which in ("ui", "backend"):
        s = root / "scripts" / f"{which}_source_id.sh"
        s.write_text(f"#!/usr/bin/env bash\necho {which}-source\n")
        s.chmod(0o755)
    (root / "frontend" / "dist" / "build-info.json").write_text(
        json.dumps({"source_id": "ui-source"}))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "frontend" / "src" / "App.tsx").write_text("export default 1;\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


class TestTheStates:
    def test_a_matching_stamp_is_current(self, repo: Path):
        r = drift.report(running_backend_id="backend-source", root=repo)
        assert [s["state"] for s in r["surfaces"]] == ["current", "current"]
        assert r["stale"] is False and r["blocked"] is False

    def test_a_moved_source_with_a_clean_tree_is_stale_not_blocked(self, repo: Path):
        """The apply step fixes this one, so it must not name any files."""
        (repo / "frontend" / "dist" / "build-info.json").write_text(
            json.dumps({"source_id": "an-older-build"}))
        r = drift.report(running_backend_id="backend-source", root=repo)
        ui = next(s for s in r["surfaces"] if s["name"] == "ui bundle")
        assert ui["state"] == "stale", ui
        assert ui["blocking_files"] == []
        assert r["stale"] is True and r["blocked"] is False

    def test_uncommitted_ui_work_makes_it_blocked_and_names_the_files(self, repo: Path):
        """THE NINE-HOUR CASE. Rerunning apply does nothing; only a commit does,
        so the files -- and therefore whose work it is -- are the answer."""
        (repo / "frontend" / "dist" / "build-info.json").write_text(
            json.dumps({"source_id": "an-older-build"}))
        (repo / "frontend" / "src" / "Half.tsx").write_text("// mid-edit\n")
        r = drift.report(running_backend_id="backend-source", root=repo)
        ui = next(s for s in r["surfaces"] if s["name"] == "ui bundle")
        assert ui["state"] == "blocked", ui
        assert ui["blocking_files"] == ["frontend/src/Half.tsx"]
        assert r["blocked"] is True
        # and `stale` stays true, because blocked is a KIND of stale: the
        # operator is still looking at old code.
        assert r["stale"] is True

    def test_an_untracked_file_counts(self, repo: Path):
        """A brand-new component nobody has committed changes what the bundle
        would contain exactly as much as an edit does, which is why
        `ui_source_id.sh` hashes untracked files too."""
        (repo / "frontend" / "dist" / "build-info.json").write_text(
            json.dumps({"source_id": "older"}))
        (repo / "frontend" / "src" / "BrandNew.tsx").write_text("export default 2;\n")
        files = drift.blocking_files(drift.UI_PATHSPEC, root=repo)
        assert files == ["frontend/src/BrandNew.tsx"]

    def test_a_missing_build_is_undecidable_not_current(self, repo: Path):
        (repo / "frontend" / "dist" / "build-info.json").unlink()
        r = drift.report(running_backend_id="backend-source", root=repo)
        ui = next(s for s in r["surfaces"] if s["name"] == "ui bundle")
        assert ui["state"] == "undecidable"

    def test_an_unknown_running_backend_is_undecidable_not_current(self, repo: Path):
        r = drift.report(running_backend_id=None, root=repo)
        be = next(s for s in r["surfaces"] if s["name"] == "backend")
        assert be["state"] == "undecidable"


class TestTheBackendGuardExcludesTests:
    def test_an_uncommitted_test_file_does_not_block_the_backend(self, repo: Path):
        """backend_source_id.sh excludes `backend/tests/` because uvicorn does
        not import it, and on 2026-08-26 an uncommitted `conftest.py` held a
        finished route unserved. The guard has to agree with the id."""
        (repo / "backend" / "tests").mkdir()
        (repo / "backend" / "tests" / "conftest.py").write_text("# wip\n")
        assert drift.blocking_files(drift.BACKEND_PATHSPEC, root=repo,
                                    exclude_tests=True) == []

    def test_an_uncommitted_service_does_block_the_backend(self, repo: Path):
        (repo / "backend" / "thing.py").write_text("# wip\n")
        assert drift.blocking_files(drift.BACKEND_PATHSPEC, root=repo,
                                    exclude_tests=True) == ["backend/thing.py"]


def test_the_pathspecs_still_match_apply_sh():
    """THE ONE THING THIS MODULE COPIES, PINNED SO IT CANNOT ROT.

    Every other fact comes from a shared script. The pathspecs do not: they live
    inside two `git status --porcelain` invocations in `scripts/apply.sh`. If
    someone widens the guard there and not here, this view would report a
    surface `stale` (rerun apply) when apply is actually going to refuse
    (`blocked`), which is precisely the wrong instruction.
    """
    text = (Path(__file__).resolve().parents[2] / "scripts" / "apply.sh").read_text()
    for token in drift.UI_PATHSPEC:
        assert token in text, (
            f"{token!r} is in services/drift.UI_PATHSPEC but no longer appears "
            f"in scripts/apply.sh. One of the two moved; they decide the same "
            f"question and must agree.")
    assert "backend/tests/" in text, (
        "apply.sh no longer excludes backend/tests from its restart guard, but "
        "services/drift still does.")


class TestTheRouter:
    def test_it_declares_a_prefix_and_a_tag(self):
        assert drift_routes.router.prefix == "/drift"
        assert drift_routes.router.tags == ["drift"]

    def test_every_route_declares_a_response_model(self):
        for route in drift_routes.router.routes:
            assert getattr(route, "response_model", None) is not None, (
                f"{route.path} has no response_model, so the generated client "
                f"types it as unknown")

    def test_it_is_mounted_in_the_app(self):
        from main import app

        assert "/drift" in {r.path for r in app.routes}

    def test_it_answers_this_installation(self):
        app = FastAPI()
        app.include_router(drift_routes.router)
        body = TestClient(app).get("/drift").json()
        assert {s["name"] for s in body["surfaces"]} == {"ui bundle", "backend"}
        for s in body["surfaces"]:
            assert s["state"] in ("current", "stale", "blocked", "undecidable")
        assert body["apply_command"] == "bash scripts/apply.sh"
