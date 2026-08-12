"""A non-developer backend refuses every door to the platform repo. ADR 0014.

This file is the acceptance criteria of the developer-mode work, and it is
organised as one test per door in §2 of
`docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md`. That
survey is the reason the design gates the resolved DIRECTORY rather than the
scope name: the platform repo was reachable under at least five names through
four subsystems, so a gate on any one name would have closed one door and left
eight open — a boundary that reads as real and is not. One test per door is
what keeps that from being a claim.

WHAT THIS DOES AND DOES NOT PROVE. It proves that with `WORKSHOP_DEV_MODE`
unset, no supported path through the application aims an autonomous run at the
Workshop's own source tree. It does NOT prove confinement: anyone with a shell
on the box edits the source directly, and real isolation is a separate execution
identity ([SEC-A1], open). This is a product capability boundary and a safety
interlock. Nothing here may be cited as a security boundary.

Dev mode is forced OFF for every test in the module (autouse fixture), because
the installation these run on is the developer's own box and would otherwise
pass by having the capability enabled.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import projects as projects_route  # noqa: E402
from routes import tasks_all  # noqa: E402
from services import app_runner, build_agents, build_streaming, knowledge, task_scope  # noqa: E402

REPO = app_runner.WORKSHOP_ROOT
SCRIPTS = REPO / "scripts"


@pytest.fixture(autouse=True)
def dev_mode_off(monkeypatch):
    monkeypatch.delenv(app_runner.DEV_MODE_ENV, raising=False)
    assert not app_runner.dev_mode_on()


@pytest.fixture
def dev_mode_on(monkeypatch):
    monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
    assert app_runner.dev_mode_on()


@pytest.fixture
def projects_client() -> TestClient:
    """The projects router at the prefix main.py mounts it at, so the paths under
    test are the paths in the §2 table."""
    app = FastAPI()
    app.include_router(projects_route.router, prefix="/projects")
    return TestClient(app, raise_server_exceptions=False)


def _self_project_slug() -> str:
    """Whatever the self registry row is called today. Asked of the predicate
    rather than hardcoded, because the whole point of the gate is that renaming
    the row cannot change the answer.

    A fresh clone has no registry at all: it is per-instance state, gitignored,
    and written when a project is first registered. Reading it unguarded made
    every test in this file fail with FileNotFoundError on any machine that had
    not been used yet, which reads as "the gate is broken" when the gate is
    fine. Skip instead: there is no self project row to gate against, so there
    is nothing here to prove.
    """
    import json
    try:
        data = json.loads(app_runner.PROJECTS_REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        pytest.skip(f"no projects registry at {app_runner.PROJECTS_REGISTRY} "
                    "(a fresh instance registers one on first use)")
    for slug in (data.get("projects") or {}):
        if app_runner.is_self_project(slug):
            return slug
    pytest.skip("no self project row in the registry on this instance")


# --------------------------------------------------------------------------
# The flag itself
# --------------------------------------------------------------------------

class TestTheFlag:
    def test_only_the_exact_string_one_reads_as_on(self, monkeypatch):
        for value in ("", "0", "false", "true", "yes", "1 ", "01"):
            monkeypatch.setenv(app_runner.DEV_MODE_ENV, value)
            assert not app_runner.dev_mode_on(), f"{value!r} must not read as on"
        monkeypatch.setenv(app_runner.DEV_MODE_ENV, "1")
        assert app_runner.dev_mode_on()

    def test_the_refusal_names_the_flag_and_disclaims_confinement(self):
        msg = app_runner.self_dir_refusal(REPO, "a chat turn")
        assert app_runner.DEV_MODE_ENV in msg
        assert str(REPO) in msg
        assert "safety interlock" in msg
        assert "not a security boundary" in msg

    def test_the_predicate_is_the_directory_not_the_name(self):
        assert app_runner.is_self_dir(REPO)
        assert app_runner.is_self_dir(str(REPO) + "/")
        assert not app_runner.is_self_dir(REPO / "frontend")
        assert not app_runner.is_self_dir(REPO.parent)


# --------------------------------------------------------------------------
# §2 door 1 — a root-scope ticket run
# --------------------------------------------------------------------------

class TestDoor1TicketRun:
    """`scope == "workshop"` used to launch a sweep with `--dir <repo root>`."""

    def test_development_does_not_resolve(self):
        assert task_scope.root_for_context("development") is None

    def test_launching_a_development_ticket_run_is_refused_with_a_reason(self):
        with pytest.raises(HTTPException) as e:
            tasks_all.launch_task_run("development", "any-id")
        assert e.value.status_code == 403
        assert app_runner.DEV_MODE_ENV in e.value.detail

    def test_workshop_is_not_a_build_target_at_all(self):
        # Not "refused because dev mode is off" — the root organising scope has
        # no code tree for a sweep to run in, in any mode (§4.1).
        assert tasks_all.scope_is_runnable("workshop") is False
        pickup = tasks_all.scope_pickup("workshop")
        assert pickup["runnable"] is False
        assert "inbox" in pickup["why"]

    def test_development_is_absent_from_the_scope_list(self):
        keys = [k for k, _ in tasks_all.scope_roots()]
        assert "development" not in keys

    def test_no_listed_scope_resolves_to_the_platform_repo(self):
        # The strongest form of door 1: whatever the scopes are called, none of
        # them is the Workshop's own tree.
        for key, root in tasks_all.scope_roots():
            assert not app_runner.is_self_dir(root), f"{key} resolves to the platform repo"


# --------------------------------------------------------------------------
# §2 door 2 — the `general` chat aliasing to the same root
# --------------------------------------------------------------------------

class TestDoor2GeneralChat:
    def test_general_no_longer_resolves_to_the_platform_repo(self):
        root = task_scope.root_for_context("general")
        assert root is not None, "the general chat must keep a queue to file into"
        assert not app_runner.is_self_dir(root)

    def test_general_and_workshop_still_share_one_inbox(self):
        # Removing the alias to the REPO must not split the root queue in two.
        assert task_scope.root_for_context("general") == task_scope.root_for_context("workshop")

    def test_the_platform_repo_maps_back_to_development_not_workshop(self):
        assert task_scope.key_for_root(REPO) == "development"


# --------------------------------------------------------------------------
# §2 door 3 — the `self: true` project registry row
# --------------------------------------------------------------------------

class TestDoor3SelfProjectRow:
    def test_there_is_exactly_one_self_row(self):
        import json
        from tests.conftest import requires_registry
        data = requires_registry()
        selves = [s for s in (data.get("projects") or {}) if app_runner.is_self_project(s)]
        assert len(selves) == 1, f"expected one name for the platform repo, found {selves}"

    def test_the_self_row_is_named_development(self):
        assert _self_project_slug() == "development"

    def test_it_does_not_resolve_as_a_project_scope(self):
        assert task_scope.root_for_context(f"project:{_self_project_slug()}") is None

    def test_a_project_scope_run_is_refused_with_a_reason(self):
        with pytest.raises(HTTPException) as e:
            tasks_all.launch_task_run(f"project:{_self_project_slug()}", "any-id")
        assert e.value.status_code == 403

    def test_it_is_not_listed_as_a_project(self, projects_client):
        # §4: exactly one name resolves to the platform repo, and it is the
        # top-level `development` scope. `GET /api/projects/` used to return the
        # self row, so the Projects page rendered a project card for the
        # platform repo and Home listed it among recent projects.
        r = projects_client.get("/projects/")
        assert r.status_code == 200, r.text
        slugs = [p["slug"] for p in r.json()]
        assert _self_project_slug() not in slugs
        assert slugs, "the filter must not empty the whole projects list"

    def test_no_listed_project_resolves_to_the_platform_repo(self, projects_client):
        # The strong form: whatever a row is called, none of the listed dirs is
        # the Workshop's own tree.
        for p in projects_client.get("/projects/").json():
            assert not app_runner.is_self_dir(p["dir"]), \
                f"project {p['slug']} is the platform repo"

    def test_it_is_still_absent_with_developer_mode_on(self, projects_client, dev_mode_on):
        # Not a dev-mode gate. The self row is not an ordinary project on a
        # developer's box either; there it is reached as the `development`
        # scope, which is a different surface with a different name.
        slugs = [p["slug"] for p in projects_client.get("/projects/").json()]
        assert _self_project_slug() not in slugs

    def test_it_is_still_addressable_by_slug(self, projects_client):
        # Deliberate (see routes/projects.get_project): the listing stops
        # OFFERING the platform repo as a project; the per-project routes stay
        # resolvable, because ADR 0014 gates the run path with a 403 that says
        # why, and a 404 from `_ensure_project` would replace that with "not
        # found" and take the autonomy config down with it.
        r = projects_client.get(f"/projects/{_self_project_slug()}")
        assert r.status_code == 200, r.text
        assert app_runner.is_self_dir(r.json()["dir"])

    def test_renaming_the_row_would_not_reopen_it(self, monkeypatch, tmp_path):
        # The gate is the directory, so a row called anything at all is refused
        # while it points at the platform repo. This is the property that makes
        # the boundary checkable instead of whack-a-mole.
        monkeypatch.setattr(build_agents, "_project_dir",
                            lambda slug: REPO if slug == "innocuous" else tmp_path)
        assert task_scope.root_for_context("project:innocuous") is None
        assert task_scope.root_for_context("project:elsewhere") == tmp_path


# --------------------------------------------------------------------------
# §2 door 4 — the retired `build:workshop-builder*` records
# --------------------------------------------------------------------------

class TestDoor4RetiredBuildAgents:
    def test_the_four_records_are_gone(self):
        for slug in ("workshop-builder", "workshop-builder-2",
                     "workshop-builder-3", "workshop-builder-4"):
            with pytest.raises(KeyError):
                build_agents.get_agent(slug)

    def test_no_build_agent_points_at_the_platform_repo(self):
        for a in build_agents.list_agents():
            cwd = a.get("cwd")
            assert not (cwd and app_runner.is_self_dir(cwd)), \
                f"build agent {a.get('slug')} has the self cwd"

    def test_a_revived_one_would_still_be_refused(self, monkeypatch):
        # `get_agent` does not filter on state, which is how four retired rows
        # stayed live doors. Deleting them is the tidy-up; this is the guard.
        monkeypatch.setattr(build_agents, "get_agent",
                            lambda slug: {"slug": slug, "cwd": str(REPO),
                                          "project_slug": None, "state": "retired"})
        assert task_scope.root_for_context("build:anything") is None


# --------------------------------------------------------------------------
# §2 door 5 — `agent:<slug>` for a maker with the self cwd
# --------------------------------------------------------------------------

class TestDoor5AgentScope:
    def test_a_builder_agent_rooted_at_the_platform_repo_is_refused(self, monkeypatch):
        from services import agents_registry
        monkeypatch.setattr(agents_registry, "get",
                            lambda slug: {"slug": slug, "runtime": "builder"})
        monkeypatch.setattr(task_scope, "_build_agent_root", lambda slug: REPO)
        assert task_scope.root_for_context("agent:maker-x") is None

    def test_an_ordinary_assistant_scope_is_untouched(self, monkeypatch):
        from services import agents_registry
        monkeypatch.setattr(agents_registry, "get",
                            lambda slug: {"slug": slug, "runtime": "assistant"})
        root = task_scope.root_for_context("agent:memory-keeper")
        assert root is not None and not app_runner.is_self_dir(root)


# --------------------------------------------------------------------------
# §2 door 6 — POST /api/projects/<self>/autonomy/run
# --------------------------------------------------------------------------

class TestDoor6AutonomyRunEndpoint:
    @pytest.fixture
    def client(self, projects_client):
        return projects_client

    def test_the_endpoint_refuses_with_a_reason_the_ui_can_show(self, client):
        r = client.post(f"/projects/{_self_project_slug()}/autonomy/run")
        assert r.status_code == 403, r.text
        assert app_runner.DEV_MODE_ENV in r.json()["detail"]

    def test_it_refuses_before_opening_a_job(self, client, monkeypatch):
        from services import jobs
        opened: list = []
        monkeypatch.setattr(jobs, "start", lambda **kw: opened.append(kw) or {"id": "x"})
        client.post(f"/projects/{_self_project_slug()}/autonomy/run")
        assert opened == [], "a refused run must not leave a Job behind"


# --------------------------------------------------------------------------
# §2 doors 7, 8, 9 — the always-on loops
# --------------------------------------------------------------------------

class TestDoors789TheLoops:
    """The loops stay on; the platform repo stops being their DEFAULT target.

    Exercised through `scripts/dev_mode_gate.sh` itself, which is the one rule
    all four scripts source, plus a check that they each actually source it and
    that neither `DIR` default was left behind."""

    def _gate(self, dir_arg: str, env: dict | None = None) -> subprocess.CompletedProcess:
        import os
        script = (f'source "{SCRIPTS}/dev_mode_gate.sh"; '
                  f'assert_run_target "{dir_arg}" "test-loop"')
        e = dict(os.environ)
        e.pop(app_runner.DEV_MODE_ENV, None)
        e.update(env or {})
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e)

    def test_a_bare_loop_with_no_target_is_refused(self):
        p = self._gate("")
        assert p.returncode == 1
        assert "no working tree named" in p.stderr

    def test_the_platform_repo_is_refused_as_a_target(self):
        p = self._gate(str(REPO))
        assert p.returncode == 1
        assert "own source tree" in p.stderr
        assert app_runner.DEV_MODE_ENV in p.stderr

    def test_an_ordinary_tree_is_allowed(self, tmp_path):
        assert self._gate(str(tmp_path)).returncode == 0

    def test_developer_mode_allows_the_platform_repo(self):
        assert self._gate(str(REPO), {app_runner.DEV_MODE_ENV: "1"}).returncode == 0

    @pytest.mark.parametrize("script", [
        "ralph.sh", "queue_runner.sh", "queue_runner_watchdog.sh", "project_ralph.sh",
    ])
    def test_every_loop_script_applies_the_gate(self, script):
        text = (SCRIPTS / script).read_text()
        assert "dev_mode_gate.sh" in text, f"{script} does not source the gate"
        assert "assert_run_target" in text, f"{script} sources the gate but never calls it"

    @pytest.mark.parametrize("script", ["ralph.sh", "queue_runner.sh"])
    def test_neither_loop_defaults_to_the_platform_repo(self, script):
        # The assignment, not the word: both scripts carry a comment explaining
        # the default they used to have, and matching prose would make this test
        # fail on its own documentation.
        import re
        text = (SCRIPTS / script).read_text()
        assigns = re.findall(r"^DIR=(\S*)", text, re.MULTILINE)
        assert assigns, f"{script} has no DIR assignment to check"
        assert assigns[0] in ('""', "''"), \
            f"{script} defaults DIR to {assigns[0]!r}; a target must be named"

    def test_no_python_launcher_leaves_the_target_unnamed(self):
        # The one Python caller of ralph.sh reads the platform repo's own
        # TODO.md, so it must say so rather than inherit a default that no
        # longer exists.
        from services.letta_tools import cloud
        src = Path(cloud.__file__).read_text()
        i = src.index("scripts/ralph.sh")
        assert "--dir" in src[i:i + 200], "cloud_run_ralph launches ralph.sh with no target"


# --------------------------------------------------------------------------
# Chokepoint 3 — every chat and build turn
# --------------------------------------------------------------------------

class TestChatTurns:
    """Doors 3, 4 and 5 all end at the same spawn: `claude` with the full
    agentic toolset and `cwd` = the repo root. `start_turn` is the funnel."""

    def _start(self, cwd: str):
        import asyncio
        return asyncio.run(build_streaming.start_turn(
            thread_id="t", agent_slug="a", prompt="p", model="m",
            cwd=cwd, session_id=None, resume=False))

    def test_a_turn_rooted_at_the_platform_repo_is_refused(self):
        with pytest.raises(HTTPException) as e:
            self._start(str(REPO))
        assert e.value.status_code == 403
        assert app_runner.DEV_MODE_ENV in e.value.detail

    def test_it_refuses_before_claiming_the_thread(self):
        # A refusal that had already claimed the run would wedge the thread as
        # busy with a turn that never starts.
        before = dict(build_streaming.TURNS)
        with pytest.raises(HTTPException):
            self._start(str(REPO))
        assert dict(build_streaming.TURNS) == before


# --------------------------------------------------------------------------
# The notes layer
# --------------------------------------------------------------------------

class TestKnowledgeScope:
    def test_development_is_not_a_notes_scope(self):
        with pytest.raises(ValueError) as e:
            knowledge._parse_scope("development")
        assert app_runner.DEV_MODE_ENV in str(e.value)

    def test_workshop_notes_are_unaffected(self):
        assert knowledge._parse_scope("workshop") == ("workshop", "")


# --------------------------------------------------------------------------
# With developer mode ON, the capability is actually there
# --------------------------------------------------------------------------

class TestDeveloperModeOn:
    """The gate has to be a switch, not a wall. If these fail, the operator's
    own queue has become invisible on the box that owns it."""

    def test_development_resolves_to_the_platform_repo(self, dev_mode_on):
        assert task_scope.root_for_context("development") == REPO

    def test_the_root_queue_is_listed_under_development(self, dev_mode_on):
        keys = [k for k, _ in tasks_all.scope_roots()]
        assert "development" in keys

    def test_development_sorts_above_workshop(self, dev_mode_on):
        keys = [k for k, _ in tasks_all.scope_roots()]
        assert keys.index("development") < keys.index("workshop")

    def test_every_open_root_ticket_is_still_pickupable(self, dev_mode_on):
        from services import tasks
        root = task_scope.root_for_context("development")
        assert tasks_all.scope_is_runnable("development") is True
        assert tasks_all.scope_pickup("development")["runnable"] is True
        all_tasks = tasks.list_tasks(root)
        if not all_tasks:
            # A fresh instance has filed nothing yet. This guards a one-time
            # migration against dropping the queue, so with no queue at all there
            # is nothing it could have dropped.
            pytest.skip("no tickets on this instance yet")
        open_tasks = [t for t in all_tasks if t.get("status") == "todo"]
        assert open_tasks, "the development queue is empty; the migration lost it"

    def test_the_platform_repo_still_has_exactly_one_name(self, dev_mode_on):
        keys = [k for k, root in tasks_all.scope_roots() if app_runner.is_self_dir(root)]
        assert keys == ["development"]

    def test_a_chat_turn_in_the_platform_repo_is_permitted(self, dev_mode_on, monkeypatch):
        # Assert only that the gate lets it through — the spawn itself is not
        # what this file is about.
        called: list = []
        monkeypatch.setattr(app_runner, "assert_run_target",
                            lambda target, who: called.append((str(target), who)))
        app_runner.assert_run_target(str(REPO), "a chat turn")
        assert called and called[0][0] == str(REPO)

    def test_development_is_a_notes_scope(self, dev_mode_on):
        assert knowledge._parse_scope("development") == ("development", "")
