"""Layer 1: bwrap scoped execution. Proves the sandbox confines a command to its
scope root mounted at /workspace, and that OFF is a pure passthrough.

These run the real bwrap when it is present and skip cleanly otherwise, so the
suite is green on a host without user namespaces."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from services import scoped_exec

_HAS_BWRAP = shutil.which("bwrap") is not None


def test_off_is_passthrough(monkeypatch):
    monkeypatch.delenv("WORKSHOP_SCOPED_EXEC", raising=False)
    assert scoped_exec.wrap(["echo", "hi"], "/tmp") == ["echo", "hi"]


def test_empty_workdir_is_passthrough(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    assert scoped_exec.wrap(["echo", "hi"], "") == ["echo", "hi"]


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
def test_scope_is_presented_at_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "inside.txt").write_text("in")

    cmd = scoped_exec.wrap(["sh", "-c", "pwd; cat /workspace/inside.txt"], str(scope))
    r = subprocess.run(cmd, cwd=str(scope), capture_output=True, text=True)
    if r.returncode != 0 and "namespace" in (r.stderr or "").lower():
        pytest.skip(f"user namespaces unavailable here: {r.stderr.strip()}")
    assert r.returncode == 0, r.stderr
    assert "/workspace" in r.stdout
    assert "in" in r.stdout


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
def test_real_path_and_outside_are_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "inside.txt").write_text("in")
    outside = tmp_path / "secret.txt"
    outside.write_text("out")

    # The real host path of the scope does not exist inside the sandbox: only
    # /workspace does. This is what makes a CLI tool print /workspace, never the
    # real /home/... path.
    real = scoped_exec.wrap(["cat", str(scope / "inside.txt")], str(scope))
    rr = subprocess.run(real, cwd=str(scope), capture_output=True, text=True)
    if "namespace" in (rr.stderr or "").lower():
        pytest.skip("user namespaces unavailable here")
    assert rr.returncode != 0

    # A sibling path outside the scope is not mounted, so it cannot be read.
    out = scoped_exec.wrap(["cat", str(outside)], str(scope))
    ro = subprocess.run(out, cwd=str(scope), capture_output=True, text=True)
    assert ro.returncode != 0
