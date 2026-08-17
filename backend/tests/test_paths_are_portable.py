"""A checkout somewhere other than ~/workshop reads its OWN tree.

Roughly half the backend used to compute its state paths as
`Path.home() / "workshop" / ...` with a private env var per module. The
installer sets `WORKSHOP_ROOT`, so a relocated clone read the right SOURCE while
writing its state to a `~/workshop/.runtime` that did not exist. `mkdir(parents=
True)` created it happily, nothing raised, and the instance came up with an empty
app registry and no apps. That is the failure this file exists to prevent, and it
cannot be caught by observation on a box where the repo IS at ~/workshop, because
there the wrong answer and the right answer are the same string.

So: point the root somewhere else and assert every derived path followed.
"""
from __future__ import annotations

import importlib
import json

import pytest

from wsplatform import paths


# Module attribute -> the module that owns it. Every one of these was a
# `Path.home() / "workshop"` literal.
DERIVED = [
    ("services.app_runner", "STATE_STORE"),
    ("services.app_runner", "LOG_ROOT"),
    ("services.app_runner", "WORKSHOP_ROOT"),
    # The interlock's own copy (target-architecture-2026-08-17.md §2.2, ADR
    # 0024): moved out of app_runner, independently derived here too rather
    # than imported across, so it must independently follow a relocated root.
    ("wsplatform.dev_mode", "WORKSHOP_ROOT"),
    ("services.port_registry", "STORE"),
    ("services.local_runner", "PROJECTS_REGISTRY"),
    ("services.local_runner", "RUNTIME_STORE"),
    ("services.local_runner", "LOG_ROOT"),
    # Callables, not constants. A path resolved at CALL time follows a root
    # that changed after import, which a module-level constant cannot — the
    # exact failure this file exists to catch, one step earlier.
    ("services.build_agents", "_scratch_root"),
    ("services.build_agents", "_registry"),
    ("services.build_agents", "_projects_registry"),
    ("wsplatform.paths", "projects_root"),
    ("wsplatform.paths", "projects_registry"),
    ("wsplatform.paths", "chat_cwd"),
    ("services.worktrees", "BRANCHES_ROOT"),
    ("services.worktrees", "PROJECTS_ROOT"),
    ("services.compose_gen", "COMPOSE_ROOT"),
    ("services.dev_servers", "LOG_ROOT"),
    ("services.commute.config", "COMMUTE_ROOT"),
    ("services.commute.config", "RUNTIME_ROOT"),
]


@pytest.mark.parametrize("module_name,attr", DERIVED)
def test_every_state_path_follows_a_relocated_root(module_name, attr, monkeypatch, tmp_path):
    root = tmp_path / "elsewhere"
    monkeypatch.setenv("WORKSHOP_ROOT", str(root))
    # Clear the per-path overrides too, or this test answers a question about
    # the AMBIENT SHELL rather than about the code. On this deployment
    # secrets.env sets WORKSHOP_CHAT_CWD, so `paths.chat_cwd` returned it
    # verbatim and the chat_cwd case failed for everyone who had that variable
    # exported and passed for everyone who did not. A gate whose verdict depends
    # on who ran it teaches people to ignore the gate.
    for override in ("WORKSHOP_CHAT_CWD", "WORKSHOP_PROJECTS_ROOT",
                     "WORKSHOP_PROJECTS_REGISTRY", "WORKSHOP_RUNTIME_DIR"):
        monkeypatch.delenv(override, raising=False)
    # These are module-level constants, so the module has to be re-imported for
    # the new root to be read. That is also the real-world behaviour: the value
    # is fixed at process start.
    mod = importlib.reload(importlib.import_module(module_name))
    try:
        value = getattr(mod, attr)
        if callable(value):
            value = value()
        assert str(value).startswith(str(root)), (
            f"{module_name}.{attr} = {value}, which ignores WORKSHOP_ROOT and would "
            f"send this instance's state to another checkout's directory"
        )
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


def test_the_root_is_always_absolute():
    """compose_gen writes these into generated compose files as bind-mount
    sources. Docker resolves a bind mount on the daemon's filesystem, and a
    relative source does not error: it silently creates an empty directory and
    starts the app against nothing."""
    assert paths.workshop_root().is_absolute()
    assert paths.runtime_dir().is_absolute()


def test_a_relative_root_is_resolved_rather_than_passed_through(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    monkeypatch.setenv("WORKSHOP_ROOT", "rel")
    assert paths.workshop_root().is_absolute()
    assert paths.workshop_root() == (tmp_path / "rel").resolve()


def test_project_sensitivity_reads_the_real_registry(monkeypatch, tmp_path):
    """The sensitivity lookup derived the registry as <projects root>/../
    projects.json, a repo-root file that has never existed. Every read raised and
    fell through to `shareable`, so a project marked private was treated as
    shareable for knowledge recall."""
    from services import permissions

    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {
        "secret": {"sensitivity": "private"},
        "open": {"sensitivity": "shareable"},
    }}), encoding="utf-8")
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))

    assert permissions._scope_sensitivity("project:secret") == "private"
    assert permissions._scope_sensitivity("project:open") == "shareable"
    # An unknown project still falls back to the safe-but-usable default.
    assert permissions._scope_sensitivity("project:nope") == "shareable"
