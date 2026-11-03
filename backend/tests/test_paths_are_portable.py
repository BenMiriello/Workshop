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
    # `RUNTIME_STORE` and `LOG_ROOT` were here until 2026-09-04. They belonged
    # to local_runner's deploy lifecycle, which had no callers and was deleted
    # with the deploy subsystem (ticket `726d75a9`).
    # Callables, not constants. A path resolved at CALL time follows a root
    # that changed after import, which a module-level constant cannot — the
    # exact failure this file exists to catch, one step earlier.
    ("services.build_agents", "_scratch_root"),
    ("services.build_agents", "_registry"),
    ("services.build_agents", "_projects_registry"),
    ("wsplatform.paths", "projects_root"),
    ("wsplatform.paths", "projects_registry"),
    # `chat_cwd` moved to `services.ownership` on 2026-09-13: every branch of
    # it below the env var is an ownership decision, and asking for one from
    # `paths` was the upward edge that held 25 modules in the import cycle.
    ("services.ownership", "chat_cwd"),
    ("services.worktrees", "BRANCHES_ROOT"),
    ("services.worktrees", "PROJECTS_ROOT"),
    ("services.compose_gen", "COMPOSE_ROOT"),
    ("services.dev_servers", "LOG_ROOT"),
    # NO APP MODULE BELONGS IN THIS LIST. Two were here until 2026-09-04 and
    # left with their app; the rule is the platform's, the example was not, and
    # a fresh clone cannot import a module it does not ship. An app asserts this
    # same rule over its own modules, in its own tests.
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


# --- a project's tree, resolved for THIS mount namespace ---------------------
#
# The same failure as above, one level in. The backend service and a sandboxed
# agent can see one tree at two absolute paths, so the registry's stored `dir`
# is correct in whichever namespace wrote it and names an empty directory -- or
# nothing -- in the other. Measured 2026-09-05: `worktrees.list_branches` and
# `gc.list_merged_worktrees` both answered "nothing here" about a tree they were
# looking at through the wrong path, and neither said so.

def test_the_self_project_resolves_to_this_namespaces_root(monkeypatch, tmp_path):
    """`self: true` means "this checkout", so the answer is derived, never the
    stored string -- which is the OTHER namespace's spelling of the same tree,
    and which here exists as an empty husk exactly as it did on the live box."""
    here = tmp_path / "here"
    here.mkdir()
    husk = tmp_path / "elsewhere"
    husk.mkdir()
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))

    resolved = paths.project_dir("development", {"self": True, "dir": str(husk)})
    assert resolved == here


def test_a_standard_project_resolves_under_this_namespaces_projects_root(
        monkeypatch, tmp_path):
    here = tmp_path / "here"
    (here / "projects" / "alpha").mkdir(parents=True)
    (tmp_path / "elsewhere" / "projects" / "alpha").mkdir(parents=True)
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))

    resolved = paths.project_dir(
        "alpha", {"dir": str(tmp_path / "elsewhere" / "projects" / "alpha")})
    assert resolved == here / "projects" / "alpha"


def test_an_external_tree_keeps_its_stored_path(monkeypatch, tmp_path):
    """A project imported from somewhere else on the host is not in the standard
    layout and has no derivable answer; the stored path is the only one there
    is, and it must still be honoured."""
    here = tmp_path / "here"
    here.mkdir()
    external = tmp_path / "opt" / "some-repo"
    external.mkdir(parents=True)
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))

    assert paths.project_dir("ext", {"dir": str(external)}) == external


def test_an_unresolvable_tree_raises_rather_than_answering_empty(
        monkeypatch, tmp_path):
    """THE POINT OF THE WHOLE THING. Returning a path that exists but is not the
    tree, or a path that does not exist at all, makes every caller answer `[]`,
    and `[]` is indistinguishable from a project with no work in it."""
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))

    with pytest.raises(paths.TreeUnresolvable) as e:
        paths.project_dir("gone", {"dir": str(tmp_path / "nowhere" / "projects" / "gone")})
    assert "gone" in str(e.value)

    with pytest.raises(paths.TreeUnresolvable):
        paths.project_dir("dirless", {})


def test_list_branches_refuses_a_tree_it_cannot_find(monkeypatch, tmp_path):
    """`worktrees.list_branches` used to return `[]` for this. The empty list
    reached the branch picker, the merged-worktree sweep and the autonomous
    run, and all three read it as "nothing to do"."""
    from services import worktrees

    here = tmp_path / "here"
    here.mkdir()
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {
        "gone": {"dir": str(tmp_path / "nowhere" / "projects" / "gone")}}}),
        encoding="utf-8")
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))
    monkeypatch.setattr(worktrees, "PROJECTS_REGISTRY", reg)

    with pytest.raises(ValueError):
        worktrees.list_branches("gone")


def test_a_dirless_self_row_names_the_branch_it_is_on(monkeypatch, tmp_path):
    """A `self: true` row needs no `dir`, since `paths.project_dir` derives it.
    The record lookup used to reject such a row as "project not found", so
    `default_ref` fell back to `HEAD` and every platform chat's branch pill
    read HEAD."""
    import subprocess

    from services import worktrees

    here = tmp_path / "here"
    here.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(here)], check=True)
    subprocess.run(["git", "-C", str(here), "-c", "user.name=t", "-c",
                    "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True)
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {"development": {"self": True}}}),
                   encoding="utf-8")
    monkeypatch.setenv("WORKSHOP_ROOT", str(here))
    monkeypatch.setattr(worktrees, "PROJECTS_REGISTRY", reg)

    assert worktrees.default_ref("development") == "trunk"
