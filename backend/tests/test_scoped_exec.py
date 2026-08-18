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


def test_zero_forces_off_passthrough(monkeypatch):
    # The escape hatch: `=0` forces the sandbox off even where it would work.
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")
    assert scoped_exec.wrap(["echo", "hi"], "/tmp") == ["echo", "hi"]


def test_default_is_on_where_functional(monkeypatch):
    # Confined by default: unset means on wherever the sandbox works. On a host
    # without user namespaces functional() is False and it degrades to passthrough,
    # so the assertion tracks the probe rather than a fixed answer.
    monkeypatch.delenv("WORKSHOP_SCOPED_EXEC", raising=False)
    wrapped = scoped_exec.wrap(["echo", "hi"], "/tmp")
    if scoped_exec.functional():
        assert wrapped[0].endswith("bwrap")
        assert "--chdir" in wrapped and scoped_exec.MOUNTPOINT in wrapped
    else:
        assert wrapped == ["echo", "hi"]


def test_empty_workdir_is_passthrough(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    assert scoped_exec.wrap(["echo", "hi"], "") == ["echo", "hi"]


def test_no_die_with_parent(monkeypatch):
    # A turn is spawned detached to survive a backend restart; --die-with-parent
    # would kill the sandbox on restart and defeat that guarantee.
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    if not _HAS_BWRAP:
        pytest.skip("bwrap not installed")
    assert "--die-with-parent" not in scoped_exec.wrap(["echo", "hi"], "/tmp")


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
def test_scope_realpath_in_argv_is_remapped(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "inside.txt").write_text("in")

    # A path under the scope root that appears in the LAUNCH argv is remapped to
    # the mountpoint, so the turn's own command resolves inside the sandbox (this
    # is what makes the MCP server's absolute host paths work). The real path is
    # not in the namespace; the remap is why referencing it still reads.
    real = scoped_exec.wrap(["cat", str(scope / "inside.txt")], str(scope))
    assert "/workspace/inside.txt" in real  # the arg was rewritten
    rr = subprocess.run(real, cwd=str(scope), capture_output=True, text=True)
    if "namespace" in (rr.stderr or "").lower():
        pytest.skip("user namespaces unavailable here")
    assert rr.returncode == 0, rr.stderr
    assert "in" in rr.stdout


def test_outside_scope_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("out")

    # A sibling path outside the scope is not mounted and not under the root, so
    # it is neither remapped nor readable.
    out = scoped_exec.wrap(["cat", str(outside)], str(scope))
    assert str(outside) in out  # untouched: outside the root
    ro = subprocess.run(out, cwd=str(scope), capture_output=True, text=True)
    if "namespace" in (ro.stderr or "").lower():
        pytest.skip("user namespaces unavailable here")
    assert ro.returncode != 0
