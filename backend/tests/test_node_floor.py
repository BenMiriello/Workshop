"""The repo declares node 20 and refuses an older one before building.

`.nvmrc`, the screenshot driver and several dependencies need node 20, while
`engines` said 18 and nothing checked it: on 18 an install silently skips an
optional package and a later command fails far from the cause.
`scripts/require_node.cjs` runs ahead of build, typecheck and test and stops
with one line naming the version needed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def _floor(spec: str) -> int:
    return int(spec.lstrip(">=^~ ").split(".")[0])


def test_engines_matches_nvmrc_and_is_at_least_20():
    engines = json.loads((REPO / "package.json").read_text())["engines"]["node"]
    nvmrc = (REPO / ".nvmrc").read_text().strip().lstrip("v")
    assert _floor(engines) >= 20
    assert _floor(engines) == _floor(nvmrc)


@pytest.mark.parametrize("script", ["pretypecheck", "prebuild:ui", "pretest"])
def test_the_root_scripts_check_first(script):
    scripts = json.loads((REPO / "package.json").read_text())["scripts"]
    assert "require_node.cjs" in scripts.get(script, "")


def test_the_frontend_build_checks_first():
    scripts = json.loads((REPO / "frontend" / "package.json").read_text())["scripts"]
    assert scripts["prebuild"].startswith("node ../scripts/require_node.cjs")


@pytest.mark.skipif(NODE is None, reason="no node on PATH")
@pytest.mark.parametrize("floor,ok", [(1, True), (999, False)])
def test_the_check_refuses_below_the_declared_floor(tmp_path, floor, ok):
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "require_node.cjs", tmp_path / "scripts")
    (tmp_path / "package.json").write_text(json.dumps({"engines": {"node": f">={floor}"}}))
    r = subprocess.run([NODE, str(tmp_path / "scripts" / "require_node.cjs")],
                       capture_output=True, text=True, timeout=30)
    assert (r.returncode == 0) is ok
    if not ok:
        assert f"needs node >={floor}" in r.stderr
