"""The maker agent: a scope's ticket-worker persona, as a record you can manage.

Two rules are pinned here and everything else follows from them.

THIS IS NOT A SECOND AUTONOMY SYSTEM. The maker's arm level is `scope_execution`
and nothing else, its residency is the work driver's, its activity is the
presence channel's. So the tests assert not just that a control works, but WHICH
FILE MOVED when it did.

THE LOOP IS THE MAKER, AND THE DEFINITION FILE IS OPTIONAL
(`primitive-roles-2026-07-18.md` §6: "projects work without any definition
file"). So nothing may create one on its own, and a scope with none must still
be dispatched -- which is measured here by running the real dispatcher's own
planner against a workshop whose `agents/` directory is empty.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def seed(tree, slug="widgets-maker", name="Widgets maker", project_slug="widgets",
         **kw):
    """Create the scope's maker the way the operator's own action does.

    `POST /agents/build` is the only caller of `makers.ensure` in the tree; this
    is that call with the slug and name the create form would have minted. It is
    a helper and not a fixture on purpose: a test that wants NO maker (most of
    the interesting ones) simply does not call it.
    """
    return tree["makers"].ensure(slug=slug, name=name, project_slug=project_slug, **kw)


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
# NOTHING CREATES A MAKER ON ITS OWN
# ---------------------------------------------------------------------------

def test_no_maker_appears_unless_someone_asks_for_one(tree):
    """THE HEADLINE RULE. There was a 60-second `reconcile_loop` that wrote a
    definition file for every runnable scope; `agent-roles-research-2026-07-16`
    calls that "a machine for generating noise and cost" and
    `routes/projects.create_project` already refuses it by hand.

    So: the module has no reconciler, exercising every read on it creates
    nothing, and the roster of a fresh workshop with a runnable scope is empty.
    """
    makers, reg = tree["makers"], tree["registry"]
    assert not hasattr(makers, "reconcile")
    assert not hasattr(makers, "reconcile_loop")

    assert [r["scope"] for r in makers.runnable_scopes()] == ["project:widgets"]
    makers.makers_for_scope("project:widgets")
    makers.maker_for_scope("project:widgets")
    makers.prompt_block("project:widgets")
    makers.scope_row("project:widgets")

    assert reg.list_agents() == []
    assert sorted((tree["proj"] / "agents").glob("*.md")) == []


def test_the_backend_lifespan_starts_no_maker_reconciler(tree):
    """Pinned as source, because the loop was STARTED FROM `main.py` and the
    module having no `reconcile` is only half of the guarantee: a future pass
    must not reintroduce a background task that writes definition files."""
    src = (REPO / "backend" / "main.py").read_text()
    # Comments may name the retired loop; a started TASK may not exist.
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "makers" in ln and "create_task" in ln]
    assert not [ln for ln in code if "_bg_maker_reconcile" in ln]
    # And the reason is on the record where the task used to be started.
    assert "THERE IS NO MAKER RECONCILER HERE" in src


def test_the_operator_action_is_the_one_thing_that_seeds_a_maker(tree):
    """`ensure` is called from exactly one place in the tree: the create-a-maker
    request. Grepping is the check, because the defect being prevented is a
    SECOND caller appearing on a timer somewhere."""
    hits = subprocess.run(
        ["grep", "-rn", "makers.ensure(", "--include=*.py", "--include=*.sh",
         str(REPO / "backend"), str(REPO / "scripts")],
        capture_output=True, text=True).stdout.strip().splitlines()
    hits = [h for h in hits if "/tests/" not in h]
    assert len(hits) == 1, hits
    assert "routes/agents.py" in hits[0]


def test_a_scope_with_no_maker_is_skipped_for_the_assignment_reason(tree, tmp_path):
    """THE MEASUREMENT BEHIND "the loop is the maker", updated 2026-09-05.

    Not a source assertion: this runs the dispatcher's OWN decision engine --
    the Python heredoc inside `scripts/dispatch_triggers.sh`, extracted
    verbatim -- against a workshop that has a project with a ready ticket and an
    EMPTY `agents/` directory.

    WHAT THIS USED TO ASSERT, and why it changed. It asserted that the sweep is
    planned anyway: a maker definition is not a prerequisite for a ticket being
    worked. The planner still never looks at `agents/`, so that property is
    intact -- but an unattended selection is now assignee-scoped
    (`docs/what-may-run-unattended-2026-09-04.md`, `tasks.next_ready`), and a
    PROJECT sweep runs as nobody. Nobody is not a worker, so the queue below is
    full of work this dispatch may not select, and planning the sweep would
    launch a Job that claims nothing and finishes as a clean zero-task success.

    So the dispatcher asks the question the sweep will answer -- `has_ready` is
    `unattended=True` there for exactly this reason -- and skips. The assertion
    is on the REASON, not merely on the empty plan: an empty plan is what a
    missing registry, an unreadable dir or a dead code path also produce, and
    this test has to keep telling those apart from the rule it is about.
    """
    root = tmp_path / "ws"
    (root / ".runtime").mkdir(parents=True)
    (root / "agents").mkdir()
    proj = root / "projects" / "widgets"
    proj.mkdir(parents=True)
    # The planner imports `services` from `$WORKSHOP/backend`, so the tmp root
    # borrows the real tree for code while owning every registry it reads.
    (root / "backend").symlink_to(REPO / "backend")
    (root / ".runtime" / "projects.json").write_text(json.dumps({"projects": {
        "widgets": {"title": "Widgets", "dir": str(proj),
                    "autonomy": {"preset": "continuous",
                                 "triggers": [{"kind": "loop"}]}}}}))

    env = {**os.environ, "WORKSHOP": str(root), "WORKSHOP_ROOT": str(root),
           "WORKSHOP_RUNTIME": str(root / ".runtime"),
           "WORKSHOP_AGENTS_DIR": str(root / "agents"),
           "WORKSHOP_PROJECTS_REGISTRY": str(root / ".runtime" / "projects.json")}
    add = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from pathlib import Path; from services import tasks;"
         "tasks.add(Path(sys.argv[2]), text='Make the widget wider')",
         str(REPO / "backend"), str(proj)],
        env=env, capture_output=True, text=True, timeout=120)
    assert add.returncode == 0, add.stderr

    assert list((root / "agents").glob("*.md")) == []
    plan = subprocess.run([sys.executable, "-c", _planner_source()],
                          env=env, capture_output=True, text=True, timeout=120)
    assert plan.returncode == 0, plan.stderr
    assert [ln.split("\t")[:2] for ln in plan.stdout.splitlines() if ln.strip()] == []
    # Named, not silent. "skipped because nobody may run it" and "idle" are
    # different facts and the operator acts on them differently.
    assert "skip-unassigned\tproject:widgets" in plan.stderr, plan.stderr


def test_a_trigger_less_maker_is_reported_as_resident_not_undispatchable(tree, tmp_path):
    """The log half of "there is one answer to what works a ticket".

    A maker seeded by the operator action carries `triggers: []` on purpose: it
    is resident, and whether it may work is `scope_execution`'s answer for its
    scope. The dispatcher used to report exactly that state as "registered but
    undispatchable", which reads as though nothing worked those tickets -- while
    the resident queue runner was working them.
    """
    root = tmp_path / "ws"
    (root / ".runtime").mkdir(parents=True)
    (root / "agents").mkdir()
    (root / "backend").symlink_to(REPO / "backend")
    proj = root / "projects" / "widgets"
    proj.mkdir(parents=True)
    (root / ".runtime" / "projects.json").write_text(json.dumps({"projects": {
        "widgets": {"title": "Widgets", "dir": str(proj)}}}))
    (root / "agents" / "widgets-maker.md").write_text(
        "---\nname: Widgets maker\npurpose: ''\ncapabilities: []\n"
        "triggers: []\nlimits: {}\nmemory: null\nrole: maker\n"
        "project_slug: widgets\nautonomy: autonomous\n---\n\nBe careful.\n")

    env = {**os.environ, "WORKSHOP": str(root), "WORKSHOP_ROOT": str(root),
           "WORKSHOP_RUNTIME": str(root / ".runtime"),
           "WORKSHOP_AGENTS_DIR": str(root / "agents"),
           "WORKSHOP_PROJECTS_REGISTRY": str(root / ".runtime" / "projects.json")}
    plan = subprocess.run([sys.executable, "-c", _planner_source()],
                          env=env, capture_output=True, text=True, timeout=120)
    assert plan.returncode == 0, plan.stderr
    assert "skip-resident\twidgets-maker" in plan.stderr
    assert "undispatchable" not in plan.stderr


def _planner_source() -> str:
    """The dispatcher's decision engine, lifted out of the bash file it lives
    in. Extracted rather than copied so the test cannot pass against a stale
    duplicate of the code it is checking."""
    src = (REPO / "scripts" / "dispatch_triggers.sh").read_text()
    body = src.split("<<'PYEOF'\n", 1)[1]
    return body.split("\nPYEOF\n", 1)[0]


def test_a_roleless_agent_logs_skip_role_not_skip_assistant(tree, tmp_path):
    """Phase R1: "Unknown or unmapped role logs `skip-role`".

    `_handler_for` used to fall through to `a.get("runtime") == "builder"`.
    `runtime` was the builder/assistant axis, which is gone from the registry,
    so the test was permanently False and EVERY roleless agent was reported as
    `skip-assistant` -- a skip attributed to an unbuilt runner for an axis that
    no longer exists. Measured through the real planner, same as above.
    """
    root = tmp_path / "ws"
    (root / ".runtime").mkdir(parents=True)
    (root / "agents").mkdir()
    (root / "backend").symlink_to(REPO / "backend")
    (root / ".runtime" / "projects.json").write_text(json.dumps({"projects": {}}))
    (root / "agents" / "wanderer.md").write_text(
        "---\nname: Wanderer\npurpose: ''\ncapabilities: []\n"
        "triggers:\n  - kind: cron\n    cron_expression: '* * * * *'\n"
        "limits: {}\nmemory: null\n---\n\nA roleless agent.\n")

    env = {**os.environ, "WORKSHOP": str(root), "WORKSHOP_ROOT": str(root),
           "WORKSHOP_RUNTIME": str(root / ".runtime"),
           "WORKSHOP_AGENTS_DIR": str(root / "agents"),
           "WORKSHOP_PROJECTS_REGISTRY": str(root / ".runtime" / "projects.json")}
    plan = subprocess.run([sys.executable, "-c", _planner_source()],
                          env=env, capture_output=True, text=True, timeout=120)
    assert plan.returncode == 0, plan.stderr
    assert "skip-role\twanderer" in plan.stderr
    assert "skip-assistant" not in plan.stderr
    # And the dead branch is gone from the code, not merely unreachable. Read
    # off `_handler_for` itself so this cannot pass on a comment that still
    # explains the retired field (the one right above does).
    body = _planner_source().split("def _handler_for(a):", 1)[1].split("\n\n\n", 1)[0]
    assert "runtime" not in body
    assert "_dispatch_assistant_loop" not in _planner_source()


# ---------------------------------------------------------------------------
# It is listed, and it is a maker
# ---------------------------------------------------------------------------

def test_the_seeded_maker_is_listed_as_a_maker_on_its_project(tree):
    makers, reg = tree["makers"], tree["registry"]
    assert [a["slug"] for a in reg.list_agents()] == []

    seed(tree)

    listed = reg.list_agents()
    assert [a["slug"] for a in listed] == ["widgets-maker"]
    assert listed[0]["role"] == "maker"
    # `GET /agents` reads exactly this, so the roster has an answer to
    # "what is doing the tickets" where it previously had none.
    assert listed[0]["project_slug"] == "widgets"


def test_the_record_is_a_real_editable_definition_file(tree):
    """(a) not (b): a FILE, because managing an agent means editing its persona,
    and a synthesized record has nothing to write to."""
    makers, reg = tree["makers"], tree["registry"]
    seed(tree)
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
    seed(tree)
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
# Several makers on one scope: legal, visible, never resolved by sort order
# ---------------------------------------------------------------------------

def test_ensure_is_additive_and_never_rewrites(tree):
    """Asked twice, it writes once. The body is the operator's document from the
    first write onward, so re-seeding would silently discard an edit."""
    makers, reg = tree["makers"], tree["registry"]
    first = seed(tree)
    reg.update("widgets-maker", body="Ship narrow diffs. Ask before renaming.")
    again = seed(tree)
    assert first["slug"] == again["slug"] == "widgets-maker"
    assert "Ship narrow diffs" in again["definition"]
    assert len([a for a in reg.list_agents() if a["role"] == "maker"]) == 1


def test_assert_role_unique_does_NOT_cover_maker_and_this_module_does_not_either(tree):
    """THE LOAD-BEARING NEGATIVE RESULT, pinned so nobody relies on the wrong
    guard -- and so nobody restores the claim that used to be in this module's
    header, that one-per-scope was "ENFORCED HERE". It was not. On the live
    install two apps each had two.

    `agents_registry.SINGLETON_ROLES` is `("triager", "keeper")` and deliberately
    EXEMPTS `maker` -- several makers on one scope is the concurrency case,
    governed by `touches` disjointness and the claim fence, not a configuration
    error. So `_assert_role_unique` raises for a second keeper and says nothing
    about a second maker, and this module does not change that rule for anyone.
    """
    makers, reg = tree["makers"], tree["registry"]
    assert "maker" not in reg.SINGLETON_ROLES

    reg.create(slug="k1", name="K1", role="keeper")
    with pytest.raises(ValueError):
        reg.create(slug="k2", name="K2", role="keeper")

    # A second maker on the same scope is NOT refused, by the registry or here.
    seed(tree)
    reg.create(slug="hand-rolled", name="Hand rolled", role="maker",
               project_slug="widgets")
    assert len([a for a in reg.list_agents() if a["role"] == "maker"]) == 2
    assert makers.ensure(slug="hand-rolled", name="Hand rolled",
                         project_slug="widgets")["slug"] == "hand-rolled"


def test_the_scopes_makers_are_all_of_them_not_whichever_file_sorts_first(tree):
    """WHY THIS EXISTS. `maker_for_scope` was a `sorted(glob)` search that
    returned the FIRST match, so an app showed `<slug>-builder-2` purely because
    `-2.md` sorts before `.md`. The roster was reporting a choice nobody made.

    Now the list is the answer, `maker_for_scope` declines to pick, and the
    peers ride on the summary block so the UI can say there are two.
    """
    makers, reg = tree["makers"], tree["registry"]
    reg.create(slug="widgets-builder-2", name="Widgets builder 2", role="maker",
               project_slug="widgets", body="second")
    reg.create(slug="widgets-builder", name="Widgets builder", role="maker",
               project_slug="widgets", body="first")

    assert [m["slug"] for m in makers.makers_for_scope("project:widgets")] == \
        ["widgets-builder", "widgets-builder-2"]
    # None means "not one", which is honest; it does NOT mean "none exists".
    assert makers.maker_for_scope("project:widgets") is None
    # And both rows carry the whole set, so neither card can look like the only
    # maker on the tree.
    for rec in makers.makers_for_scope("project:widgets"):
        assert makers.summary(rec)["scope_makers"] == \
            ["widgets-builder", "widgets-builder-2"]


def test_one_maker_is_the_ordinary_case_and_says_so(tree):
    makers = tree["makers"]
    seed(tree)
    assert [m["slug"] for m in makers.makers_for_scope("project:widgets")] == \
        ["widgets-maker"]
    assert makers.maker_for_scope("project:widgets")["slug"] == "widgets-maker"
    assert makers.summary(makers.maker_for_scope("project:widgets"))["scope_makers"] \
        == ["widgets-maker"]


def test_an_operator_written_maker_under_any_name_is_this_scopes_maker(tree):
    """`makers_for_scope` is a SEARCH, not a slug lookup. That is why five of
    the seven live scopes kept the maker the operator already had instead of
    growing a `<slug>-maker` duplicate beside it."""
    makers, reg = tree["makers"], tree["registry"]
    reg.create(slug="my-own-worker", name="My own worker", role="maker",
               project_slug="widgets", body="do it my way")
    assert makers.maker_for_scope("project:widgets")["slug"] == "my-own-worker"
    assert "do it my way" in makers.prompt_block("project:widgets")


def test_no_slug_convention_is_minted_in_this_module(tree):
    """The seeding path takes the slug it is given. The previous version minted
    `<project-slug>-maker`, a name no doc chose, which reached two of seven live
    scopes while the other five kept `<slug>-builder`: one roster, two
    conventions. `app-is-the-entity-2026-08-20.md` -- a slug is a label, never an
    identity -- so the label comes from the operator's name upstream."""
    makers, reg = tree["makers"], tree["registry"]
    src = (REPO / "backend" / "services" / "makers.py").read_text()
    assert "MAKER_SLUG_SUFFIX" not in src
    assert '"-maker"' not in src and "'-maker'" not in src
    assert not hasattr(makers, "slug_for")

    seed(tree, slug="widget-wrangler", name="Widget wrangler")
    assert reg.get("widget-wrangler")["role"] == "maker"


# ---------------------------------------------------------------------------
# The arm level IS scope_execution, and is stored nowhere else
# ---------------------------------------------------------------------------

def test_arm_level_round_trips_through_scope_execution_and_no_new_store(tree):
    """THE RULE THIS WHOLE DESIGN TURNS ON. Setting the maker's arm level must
    move `scope-execution.json` and create no file of its own."""
    makers, reg, sexec = tree["makers"], tree["registry"], tree["exec"]
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
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

    seed(tree)
    reg.update("widgets-maker", body="")
    assert makers.prompt_block("project:widgets") == ""       # empty body

    def boom(*a, **k):
        raise RuntimeError("registry unavailable")
    monkeypatch.setattr(makers.agents_registry, "list_agents", boom)
    assert makers.prompt_block("project:widgets") == ""


def test_two_personas_for_one_tree_means_no_persona_is_injected(tree):
    """The worker prompt must not be decided by which slug sorts first.

    A definition file is optional -- the loop is the maker -- so refusing to
    choose costs the sweep nothing it was entitled to. Choosing would mean an
    unattended run being told to be `widgets-builder-2` because `-2` sorts
    before nothing, which is the exact defect on the live install.
    """
    makers, reg = tree["makers"], tree["registry"]
    reg.create(slug="widgets-builder", name="Widgets builder", role="maker",
               project_slug="widgets", body="be careful")
    assert "be careful" in makers.prompt_block("project:widgets")

    reg.create(slug="widgets-builder-2", name="Widgets builder 2", role="maker",
               project_slug="widgets", body="move fast")
    assert makers.prompt_block("project:widgets") == ""


def test_the_cli_says_which_kind_of_nothing_it_found(tree):
    """`maker: null` used to mean only "none". It can also mean "several and the
    platform will not pick", and a shell caller has to be able to tell those
    apart."""
    makers, reg = tree["makers"], tree["registry"]
    out = json.loads(_capture(makers, ["state", "--scope", "project:widgets"]))
    assert out == {"scope": "project:widgets", "maker": None, "scope_makers": []}

    reg.create(slug="m-a", name="A", role="maker", project_slug="widgets")
    reg.create(slug="m-b", name="B", role="maker", project_slug="widgets")
    out = json.loads(_capture(makers, ["state", "--scope", "project:widgets"]))
    assert out["maker"] is None and out["scope_makers"] == ["m-a", "m-b"]
    listed = json.loads(_capture(makers, ["makers", "--scope", "project:widgets"]))
    assert [m["slug"] for m in listed] == ["m-a", "m-b"]


def _capture(makers, argv):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert makers.main(argv) == 0
    return buf.getvalue()


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
    seed(tree)
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
    seed(tree)
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
    seed(tree)
    store = tree["root"] / ".runtime" / "scope-execution.json"

    body = _client(tree).post("/agents/widgets-maker/maker/execution",
                              json={"mode": "supervised"}).json()
    assert body["arm_level"] == "supervised"
    assert json.loads(store.read_text()) == {"project:widgets": "supervised"}
    assert sexec.get_mode("project:widgets") == "supervised"


def test_creating_a_maker_with_a_tree_writes_its_definition(tree):
    """THE OPERATOR ACTION, end to end. `/agents/new` with role Maker and a
    project posts here; before this it wrote only an execution row into
    `build_agents.json` and `list_agents` has no builder fallback on purpose, so
    the maker he had just created never reached the roster at all."""
    client = _client(tree)
    r = client.post("/agents/build", json={"slug": "widget-wrangler",
                                           "name": "Widget wrangler",
                                           "purpose": "keep widgets wide",
                                           "project_slug": "widgets"})
    assert r.status_code == 200, r.text

    rec = tree["registry"].get("widget-wrangler")
    assert rec is not None and rec["role"] == "maker"
    assert rec["project_slug"] == "widgets"
    assert (tree["proj"] / "agents" / "widget-wrangler.md").exists()
    # It is on the roster with a live maker block, which is the whole point.
    row = next(a for a in client.get("/agents/").json() if a["slug"] == "widget-wrangler")
    assert row["maker"]["scope"] == "project:widgets"
    assert row["maker"]["scope_makers"] == ["widget-wrangler"]


def test_a_second_maker_on_a_project_is_allowed_and_both_say_so(tree):
    """Creating another maker for a project the operator already gave one is
    the concurrency case, not an error. What must not happen is one of them
    quietly looking like the only one."""
    client = _client(tree)
    for slug, name in (("widget-wrangler", "Widget wrangler"),
                       ("widget-polisher", "Widget polisher")):
        assert client.post("/agents/build", json={
            "slug": slug, "name": name, "project_slug": "widgets"}).status_code == 200
    rows = {a["slug"]: a for a in client.get("/agents/").json()}
    for slug in ("widget-wrangler", "widget-polisher"):
        assert rows[slug]["maker"]["scope_makers"] == ["widget-polisher", "widget-wrangler"]
    # And neither persona reaches the worker while it is ambiguous.
    assert tree["makers"].prompt_block("project:widgets") == ""


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
