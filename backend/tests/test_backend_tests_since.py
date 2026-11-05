"""check_backend_tests --since: a failure is forgiven only if it fails at the base too."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_backend_tests_base", _SCRIPTS / "_backend_tests_base.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "x.txt").write_text("x")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    for argv in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env)
    return repo


def _parse(out: str):
    failed = {l.split()[1] for l in out.splitlines() if l.startswith("FAILED ")}
    return failed, set(), 1


def test_only_ids_that_fail_at_the_base_are_reported(tmp_path):
    repo = _repo(tmp_path)
    ran = []

    def fake_pytest(cwd, targets, timeout):
        ran.append((cwd, targets))
        assert (cwd.parent / "backend" / "x.txt").exists()
        return "FAILED tests/a.py::old - boom\n1 passed"

    got = _load().failing_at(
        "HEAD", ["tests/a.py::old", "tests/b.py::new"], repo, 30, fake_pytest, _parse)
    assert got == {"tests/a.py::old"}
    assert ran[0][1] == ["tests/a.py::old", "tests/b.py::new"]


def test_a_base_that_cannot_be_checked_forgives_nothing(tmp_path):
    repo = _repo(tmp_path)
    mod = _load()
    assert mod.failing_at("nosuchrev", ["tests/a.py::t"], repo, 30,
                          lambda *a: "", _parse) is None

    def timeout(cwd, targets, t):
        raise subprocess.TimeoutExpired("pytest", t)

    assert mod.failing_at("HEAD", ["tests/a.py::t"], repo, 30, timeout, _parse) is None
    assert mod.failing_at("HEAD", ["tests/a.py::t"], repo, 30,
                          lambda *a: "garbage", lambda o: (set(), set(), 0)) is None


def test_isolation_ids_are_never_selected_or_forgiven(tmp_path):
    repo = _repo(tmp_path)
    got = _load().failing_at("HEAD", ["instance_isolation::/x"], repo, 30,
                             lambda *a: 1 / 0, _parse)
    assert got == set()
