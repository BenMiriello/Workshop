"""The maker agent: the ticket worker, as a record you can see and manage.

What is pinned here is the one rule the design turns on -- THIS IS NOT A SECOND
AUTONOMY SYSTEM. The maker's arm level is `scope_execution` and nothing else, its
residency is the work driver's, its activity is the presence channel's. So the
tests assert not just that a control works, but WHICH FILE MOVED when it did.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A throwaway workshop: its own root, agents dir, registries and channels.

    Reloaded in dependency order because several of these modules bind their
    paths at import time. `makers` last: it does `from services import ...` at
    module scope, so reloading a dependency after it would leave it holding the
    previous module object.
    """
    root = tmp_path / "workshop"
    (root / ".runtime").mkdir(parents=True)
    (root / "agents").mkdir()
    monkeypatch.setenv("WORKSHOP_ROOT", str(root))
    monkeypatch.setenv("WORKSHOP_AGENTS_DIR", str(root / "agents"))
    monkeypatch.setenv("WORKSHOP_SUBSCRIPTIONS", str(root / ".runtime" / "subs.json"))
    monkeypatch.setenv("WORKSHOP_CHANNEL_AGENT_MAP", str(root / ".runtime" / "cam.json"))
    monkeypatch.setenv("WORKSHOP_HEARTBEATS_DIR", str(root / ".runtime" / "heartbeats"))
    monkeypatch.setenv("WORKSHOP_JOBS_LOG", str(root / ".runtime" / "jobs.jsonl"))
    monkeypatch.setenv("WORKSHOP_DEV_MODE", "0")

    # One project with a real tree, so `key_for_root` maps it to `project:<slug>`.
    proj = root / "projects" / "widgets"
    proj.mkdir(parents=True)
    (proj / ".tasks.json").write_text(json.dumps({"tasks": []}))
    (root / ".runtime" / "projects.json").write_text(json.dumps({
        "projects": {"widgets": {"title": "Widgets", "dir": str(proj)}}
    }))

    from wsplatform import paths as _paths
    from services import (agents_registry, build_agents, jobs, makers, presence,
                          scope_execution, task_scope, work_driver)
    chain = (_paths, task_scope, build_agents, scope_execution, jobs,
             presence, agents_registry, work_driver, makers)
    for mod in chain:
        importlib.reload(mod)
    yield {"root": root, "proj": proj, "makers": makers,
           "registry": agents_registry, "exec": scope_execution,
           "presence": presence, "driver": work_driver, "jobs": jobs}
    # PUT THE ENVIRONMENT BACK AND RELOAD AGAIN, in that order. Several of these
    # modules bind their paths at IMPORT time, so a reload under the throwaway
    # env leaves them pointing at a tmpdir for every test that runs afterwards --
    # a whole-suite failure caused by a fixture rather than by the code under
    # test. Measured: five unrelated tests in three files went red on the first
    # version of this fixture, and still did when the reload was here but the
    # `monkeypatch.undo()` was not: a yield-fixture's teardown runs BEFORE the
    # teardown of the fixtures it depends on, so `WORKSHOP_ROOT` was still the
    # tmpdir at this point and the "restoring" reload re-pinned it.
    monkeypatch.undo()
    for mod in chain:
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# It exists, it is listed, and it is a maker
# ---------------------------------------------------------------------------

def test_the_reconciler_gives_every_runnable_scope_a_maker(tree):
    makers, reg = tree["makers"], tree["registry"]
    assert [a["slug"] for a in reg.list_agents()] == []

    out = makers.reconcile()
    assert out["created"] == 1

    listed = reg.list_agents()
    assert [a["slug"] for a in listed] == ["widgets-maker"]
    assert listed[0]["role"] == "maker"
    # `GET /agents` reads exactly this, so the roster now has an answer to
    # "what is doing the tickets" where it previously had none.
    assert listed[0]["project_slug"] == "widgets"


def test_the_record_is_a_real_editable_definition_file(tree):
    """(a) not (b): a FILE, because managing an agent means editing its persona,
    and a synthesized record has nothing to write to."""
    makers, reg = tree["makers"], tree["registry"]
    makers.reconcile()
    rec = reg.get("widgets-maker")
    # Project-scoped, because the maker is linked to a project and that is where
    # `agents_registry._path_for` puts a project-linked agent's definition.
    path = tree["proj"] / "agents" / "widgets-maker.md"
    assert path.exists() and rec["definition_path"] == str(path)
    assert rec["definition"].strip()

    reg.update("widgets-maker", body="Ship narrow diffs. Ask before renaming.")
    assert "Ship narrow diffs" in reg.get("widgets-maker")["definition"]


def test_the_maker_knows_which_scope_it_works(tree):
    makers, reg = tree["makers"], tree["registry"]
    makers.reconcile()
    rec = reg.get("widgets-maker")
    # Read off the project link the definition already carries, inverted through
    # task_scope. No new frontmatter field, no second mapping to keep in step.
    assert makers.scope_for(rec) == "project:widgets"
    assert makers.scope_row("project:widgets")["dir"] == str(tree["proj"].resolve())


def test_a_non_maker_has_no_execution_scope(tree):
    makers, reg = tree["makers"], tree["registry"]
    reg.create(slug="a-keeper", name="Keeper", role="keeper")
    assert makers.scope_for(reg.get("a-keeper")) is None


# ---------------------------------------------------------------------------
# Idempotence: never two makers for one scope
# ---------------------------------------------------------------------------

def test_reconcile_is_idempotent(tree):
    makers, reg = tree["makers"], tree["registry"]
    first = makers.reconcile()
    second = makers.reconcile()
    third = makers.reconcile()
    assert (first["created"], second["created"], third["created"]) == (1, 0, 0)
    assert len([a for a in reg.list_agents() if a["role"] == "maker"]) == 1


def test_assert_role_unique_does_NOT_cover_maker_so_this_module_does(tree):
    """THE LOAD-BEARING NEGATIVE RESULT, pinned so nobody relies on the wrong
    guard.

    `agents_registry.SINGLETON_ROLES` is `("triager", "keeper")` and deliberately
    EXEMPTS `maker` -- several makers on one scope is the concurrency case,
    governed by `touches` disjointness and the claim fence, not a configuration
    error. So `_assert_role_unique` raises for a second keeper and says nothing
    about a second maker, and the reconciler's idempotence cannot come from it.
    It comes from `maker_for_scope` asking first.
    """
    makers, reg = tree["makers"], tree["registry"]
    assert "maker" not in reg.SINGLETON_ROLES

    reg.create(slug="k1", name="K1", role="keeper")
    with pytest.raises(ValueError):
        reg.create(slug="k2", name="K2", role="keeper")

    # A second maker on the same scope is NOT refused by the registry...
    makers.reconcile()
    reg.create(slug="hand-rolled", name="Hand rolled", role="maker",
               project_slug="widgets")
    assert len([a for a in reg.list_agents() if a["role"] == "maker"]) == 2
    # ...and the reconciler still never adds a third, because it asks first.
    assert makers.reconcile()["created"] == 0


def test_an_operator_written_maker_is_adopted_rather_than_duplicated(tree):
    """A maker the operator wrote by hand, under any name, IS this scope's
    maker. Creating a second beside it would be the roster growing a duplicate
    of the very thing he said he could not find."""
    makers, reg = tree["makers"], tree["registry"]
    reg.create(slug="my-own-worker", name="My own worker", role="maker",
               project_slug="widgets", body="do it my way")
    assert makers.reconcile()["created"] == 0
    assert makers.maker_for_scope("project:widgets")["slug"] == "my-own-worker"


# ---------------------------------------------------------------------------
# The arm level IS scope_execution, and is stored nowhere else
# ---------------------------------------------------------------------------

def test_arm_level_round_trips_through_scope_execution_and_no_new_store(tree):
    """THE RULE THIS WHOLE DESIGN TURNS ON. Setting the maker's arm level must
    move `scope-execution.json` and create no file of its own."""
    makers, reg, sexec = tree["makers"], tree["registry"], tree["exec"]
    makers.reconcile()
    rec = reg.get("widgets-maker")

    runtime = tree["root"] / ".runtime"
    before = {p.name for p in runtime.iterdir()}
    store = runtime / "scope-execution.json"
    assert not store.exists()

    assert makers.summary(rec)["arm_level"] == "reactive"      # fail-closed default
    sexec.set_mode("project:widgets", "supervised")

    assert store.exists()
    assert json.loads(store.read_text()) == {"project:widgets": "supervised"}
    # The maker READS that file. It does not keep a copy.
    s = makers.summary(rec)
    assert (s["arm_level"], s["armed"], s["ceiling"]) == ("supervised", True, "in_review")

    sexec.set_mode("project:widgets", "autonomous")
    assert makers.summary(rec)["ceiling"] == "done"

    # Disarming is stored as ABSENCE, which is what makes reactive fail-closed.
    sexec.set_mode("project:widgets", "reactive")
    assert json.loads(store.read_text()) == {}
    assert makers.summary(rec)["armed"] is False

    # And in all of that, exactly one new STORE appeared under .runtime. (The
    # `.lock` files are the work driver's residency probe, which opens the tree's
    # flock to ask whether anyone holds it; they hold no state.)
    after = {p.name for p in runtime.iterdir() if not p.name.endswith(".lock")}
    assert after - before == {"scope-execution.json"}


def test_the_shared_control_arms_and_disarms_the_same_store(tree, monkeypatch):
    """Start ARMS and Stop DISARMS, so neither is a button the next reconcile
    pass undoes -- and both go through the ONE implementation the autonomy
    routes also call, so an agent-level control cannot drift from it."""
    makers, driver, sexec = tree["makers"], tree["driver"], tree["exec"]
    makers.reconcile()
    root = str(tree["proj"])
    monkeypatch.setattr(driver, "ensure", lambda r, m="supervised": True)
    monkeypatch.setattr(driver, "stop", lambda r: True)

    driver.start_work("project:widgets", root)
    assert sexec.get_mode("project:widgets") == "supervised"

    # Start does NOT downgrade a scope the operator took all the way to done.
    sexec.set_mode("project:widgets", "autonomous")
    driver.start_work("project:widgets", root)
    assert sexec.get_mode("project:widgets") == "autonomous"

    driver.stop_work("project:widgets", root, settle=0)
    assert sexec.get_mode("project:widgets") == "reactive"
    assert sexec.is_armed("project:widgets") is False


# ---------------------------------------------------------------------------
# Activity: observed, never invented
# ---------------------------------------------------------------------------

def test_a_sweeps_presence_row_surfaces_as_the_makers_current_ticket(tree):
    makers, reg, presence = tree["makers"], tree["registry"], tree["presence"]
    makers.reconcile()
    rec = reg.get("widgets-maker")
    assert makers.summary(rec)["activity"] == "absent"

    # Exactly what scripts/project_ralph.sh writes when it picks up a ticket:
    # `--kind run --subject-kind ticket --subject-id <id> --scope <scope key>`.
    presence.beat("sweep-job_1", session_kind="run", activity="working ticket t-7",
                  subject_kind="ticket", subject_id="t-7", scope="project:widgets")

    s = makers.summary(rec)
    assert s["activity"] == "working"
    assert s["ticket_id"] == "t-7"
    assert "holds a ticket" in s["activity_detail"]

    state = makers.live_state(rec)
    assert [e["session_id"] for e in state["sessions"]] == ["sweep-job_1"]
    assert state["sessions"][0]["activity"] == "working ticket t-7"


def test_a_ticket_in_the_scopes_store_gives_the_row_its_subject(tree):
    """The directory join resolves the ticket's title and status at read time
    and stores nothing, so the UI can say WHAT it is working on."""
    from services import tasks

    makers, reg, presence = tree["makers"], tree["registry"], tree["presence"]
    makers.reconcile()
    task = tasks.add(tree["proj"], text="Make the widget wider")
    presence.beat("sweep-job_2", session_kind="run", subject_kind="ticket",
                  subject_id=task["id"], scope="project:widgets")
    s = makers.summary(reg.get("widgets-maker"))
    assert s["ticket_title"] == "Make the widget wider"
    assert s["ticket_status"] == task["status"]


def test_present_does_not_pretend_to_know_idle_from_thinking(tree, monkeypatch):
    """The honest field. A resident runner that has not claimed a ticket is
    `present`, and the detail says the channel cannot tell "waiting on the poll"
    from "sweeping, nothing claimed yet" -- rather than the API picking one."""
    makers, reg, driver = tree["makers"], tree["registry"], tree["driver"]
    makers.reconcile()
    monkeypatch.setattr(driver, "is_resident", lambda d: True)
    s = makers.summary(reg.get("widgets-maker"))
    assert s["activity"] == "present"
    assert s["runner_resident"] is True
    assert "cannot tell those apart" in s["activity_detail"]
    assert makers.ACTIVITIES == ("absent", "present", "working")


def test_an_expired_presence_row_is_not_activity(tree):
    """Liveness is the record's own expiry. Reading never reconciles, so an
    expired sweep simply stops being here."""
    makers, reg, presence = tree["makers"], tree["registry"], tree["presence"]
    makers.reconcile()
    presence.beat("sweep-old", session_kind="run", subject_kind="ticket",
                  subject_id="t-9", scope="project:widgets", ttl_sec=1)
    path = presence._session_path("sweep-old")
    rec = json.loads(path.read_text())
    rec["expires_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(rec))
    assert makers.summary(reg.get("widgets-maker"))["activity"] == "absent"


def test_recent_runs_are_the_jobs_that_worked_this_scopes_tickets(tree):
    """Attribution is structural: a ticket lives in exactly one scope's store,
    so a Job carrying that ticket id is this maker's run. Its coordinator sweep
    comes in by parent_id. No tag the sweep has to remember to write."""
    from services import tasks

    makers, reg, jobs = tree["makers"], tree["registry"], tree["jobs"]
    makers.reconcile()
    mine = tasks.add(tree["proj"], text="Mine")
    parent = jobs.start(trigger="loop")
    child = jobs.start(trigger="loop", task_id=mine["id"], parent_id=parent["id"])
    jobs.start(trigger="loop", task_id="t-from-another-scope")

    ids = {r["id"] for r in makers.live_state(reg.get("widgets-maker"))["recent_runs"]}
    assert ids == {parent["id"], child["id"]}


# ---------------------------------------------------------------------------
# The persona reaches the worker
# ---------------------------------------------------------------------------

def test_the_prompt_block_carries_the_makers_edited_body(tree):
    """What makes the record real rather than decorative: `scripts/ralph.sh`
    injects this into the unattended worker's prompt, so editing the agent is
    what changes the worker."""
    makers, reg = tree["makers"], tree["registry"]
    makers.reconcile()
    reg.update("widgets-maker", body="Never touch the migrations directory.")
    block = makers.prompt_block("project:widgets")
    assert "Never touch the migrations directory." in block
    assert "project:widgets" in block


def test_the_prompt_build_tolerates_a_missing_definition(tree, monkeypatch):
    """FAILS SOFT ON EVERY PATH. No maker, an empty body, or a store that raises:
    the block is empty and the sweep runs exactly as it does today. This runs
    unattended with nobody to answer a traceback."""
    makers, reg = tree["makers"], tree["registry"]

    assert makers.prompt_block("project:widgets") == ""       # no maker at all
    assert makers.prompt_block("project:nope") == ""          # no such scope
    assert makers.prompt_block("") == ""

    makers.reconcile()
    reg.update("widgets-maker", body="")
    assert makers.prompt_block("project:widgets") == ""       # empty body

    def boom(*a, **k):
        raise RuntimeError("registry unavailable")
    monkeypatch.setattr(makers.agents_registry, "list_agents", boom)
    assert makers.prompt_block("project:widgets") == ""


def test_the_cli_prints_nothing_and_exits_zero_when_there_is_no_maker(tree, capsys):
    """The shell contract `scripts/ralph.sh` relies on: empty stdout, rc 0."""
    assert tree["makers"].main(["prompt", "--scope", "project:widgets"]) == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# The scope enumeration is shared with the runner picker
# ---------------------------------------------------------------------------

def test_the_platform_repo_is_offered_only_in_developer_mode(tree, monkeypatch):
    makers = tree["makers"]
    assert [r["scope"] for r in makers.runnable_scopes()] == ["project:widgets"]
    monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
    scopes = [r["scope"] for r in makers.runnable_scopes()]
    assert scopes == ["development", "project:widgets"]


def test_a_registry_row_with_no_dir_is_skipped_not_guessed_at(tree):
    makers = tree["makers"]
    reg_path = tree["root"] / ".runtime" / "projects.json"
    data = json.loads(reg_path.read_text())
    data["projects"]["ghost"] = {"title": "Ghost"}
    reg_path.write_text(json.dumps(data))
    assert [r["project_slug"] for r in makers.runnable_scopes()] == ["widgets"]


# ---------------------------------------------------------------------------
# The route surface: what the frontend actually consumes
# ---------------------------------------------------------------------------

def _client(tree):
    """A bare app holding only the agents router.

    `response_model` FILTERS and FastAPI 500s on a mismatch, so calling the
    routes is the only sound check that the declared models match what the
    handlers return (`scripts/check_routes_answer.py`'s own reasoning).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agents_routes

    # NOT reloaded. This router reads every path at CALL time and holds its
    # services by module object, which `importlib.reload` mutates in place, so
    # the `tree` fixture's reloads are already visible here -- and reloading a
    # route module is how a fixture leaks a tmpdir into the rest of the suite.
    app = FastAPI()
    app.include_router(agents_routes.router, prefix="/agents")
    return TestClient(app)


def test_get_agents_lists_the_maker_with_its_live_block(tree):
    makers = tree["makers"]
    makers.reconcile()
    client = _client(tree)

    rows = client.get("/agents/").json()
    row = next(r for r in rows if r["slug"] == "widgets-maker")
    assert row["role"] == "maker"
    # The block that answers "what is doing the tickets" from the list itself.
    assert row["maker"]["scope"] == "project:widgets"
    assert row["maker"]["arm_level"] == "reactive"
    assert row["maker"]["activity"] == "absent"


def test_the_maker_detail_route_answers_with_its_declared_model(tree):
    makers, presence = tree["makers"], tree["presence"]
    makers.reconcile()
    presence.beat("sweep-job_9", session_kind="run", activity="working ticket t-3",
                  subject_kind="ticket", subject_id="t-3", scope="project:widgets")
    body = _client(tree).get("/agents/widgets-maker/maker").json()
    assert body["activity"] == "working"
    assert body["ticket_id"] == "t-3"
    assert body["sessions"][0]["session_id"] == "sweep-job_9"
    assert body["runner"]["running"] is False
    assert body["recent_runs"] == []


def test_setting_the_arm_level_on_the_agent_writes_scope_execution(tree):
    """The control on the AGENT and the control in the Settings drawer are the
    same write. There is no per-agent copy that could disagree."""
    makers, sexec = tree["makers"], tree["exec"]
    makers.reconcile()
    store = tree["root"] / ".runtime" / "scope-execution.json"

    body = _client(tree).post("/agents/widgets-maker/maker/execution",
                              json={"mode": "supervised"}).json()
    assert body["arm_level"] == "supervised"
    assert json.loads(store.read_text()) == {"project:widgets": "supervised"}
    assert sexec.get_mode("project:widgets") == "supervised"


def test_an_agent_that_is_not_a_maker_gets_a_409_not_a_404(tree):
    """The agent exists; the sub-resource does not. Reporting it as missing
    would send a caller looking for a typo in a slug that is correct."""
    tree["registry"].create(slug="a-keeper", name="Keeper", role="keeper")
    client = _client(tree)
    assert client.get("/agents/a-keeper/maker").status_code == 409
    assert client.get("/agents/nobody/maker").status_code == 404


# ---------------------------------------------------------------------------
# The shell contract: ralph.sh's prompt build
# ---------------------------------------------------------------------------

def test_ralph_injects_the_maker_persona_and_fails_soft(tree):
    """`scripts/ralph.sh` is where the record stops being decorative.

    Pinned as source, because the block runs a thousand lines into an unattended
    sweep and cannot be exercised end to end here: what matters is that it is
    injected into the SAME prompt string the learnings go into, that every way
    the read can fail is swallowed, and that the injection is conditional on the
    block being non-empty -- so an absent definition changes nothing at all.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "ralph.sh").read_text()
    assert "-m services.makers prompt --scope" in src
    body = src.split("--- The maker agent's persona and rules")[1].split("iter=0")[0]
    assert "2>/dev/null" in body and "|| true" in body      # never raises
    assert 'if [[ -n "$MAKER_BLOCK" ]]; then' in body       # never injects nothing
    assert 'RALPH_INSTRUCTIONS="$RALPH_INSTRUCTIONS' in body
    # The comment that states the point, because the point is the whole feature.
    assert "EDITING THE AGENT IS WHAT CHANGES THIS WORKER" in body


def test_the_shell_command_ralph_runs_is_silent_and_zero_for_an_unknown_scope(tree):
    """The literal invocation, run as a subprocess: empty stdout, exit 0. A
    sweep captures stdout and treats empty as "nothing to inject"."""
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "services.makers", "prompt", "--scope", "project:nope"],
        cwd=str(backend.parent), env={"PYTHONPATH": str(backend), "PATH": "/usr/bin:/bin",
                                      "WORKSHOP_ROOT": str(tree["root"])},
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout == ""
