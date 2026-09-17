"""A project chat can set its own project's repo through the same validated
write the UI's Repository panel uses, and the Bash escape hatch that used to
bypass the registry is closed in the same change.

Two things have to be true:

  - the MCP `set_repo` tool and `PATCH /projects/{slug}` both go through
    `routes.projects.set_repo` -> `services.github.bind_repo` + `_repo_field`,
    so owner policy (`reject_blocked`) applies on both doors identically, and
    the write actually lands in the registry record -- what worktrees and
    publish read, not just `.git/config`;
  - `git remote add/set-url` is denied on an unattended run, so the
    unsupported path (Bash rewriting `.git/config` while the registry stays
    stale) is closed rather than merely superseded.
"""
from __future__ import annotations

import json

import pytest

from mcp import workshop_query_server as wqs
from services import agent_authority


# ---- the Bash escape hatch is closed ----------------------------------------

def test_git_remote_is_denied_on_an_unattended_run():
    assert "Bash(git remote:*)" in agent_authority.IRREVERSIBLE_PATTERNS
    assert "Bash(git remote:*)" in agent_authority.disallowed_for(unattended=True)
    assert "Bash(git remote:*)" not in agent_authority.disallowed_for(unattended=False)


def test_the_supported_door_is_declared_in_the_tool_catalog():
    """tools-are-declared-2026-08-23: every tool the workshop server can
    dispatch to is named in PLATFORM_TOOLS, with its own prose. Not the
    enforcement point (the module's docstring is explicit about that -- a
    project chat gets the grant from `_tool_list`'s own scope check, full
    agentic mode takes no allowlist), but the enumeration must not fall behind
    what genuinely exists."""
    from services import tool_registry as tr

    tool_id = f"mcp__{wqs.SERVER_NAME}__{wqs.SET_REPO_TOOL_NAME}"
    tool = next((t for t in tr.PLATFORM_TOOLS if t.id == tool_id), None)
    assert tool is not None, f"{tool_id} is exposed by the server but not declared"
    assert tool.description == wqs.SET_REPO_TOOL_DESC


# ---- shared fixture: a registered project the tool can act on --------------

@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """A `widgets` project registered in a throwaway registry, with a real
    (non-git) directory on disk -- `set_repo` only needs the dir to exist; the
    actual `git`/`gh` work is `services.github.bind_repo`'s, mocked per test."""
    proj_dir = tmp_path / "projects" / "widgets"
    proj_dir.mkdir(parents=True)
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {
        "widgets": {
            "title": "Widgets", "dir": str(proj_dir), "purpose": "",
            "sensitivity": "shareable", "state": "sketch",
            "created": "2026-01-01T00:00:00+00:00",
            "last_touched": "2026-01-01T00:00:00+00:00",
        },
    }}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(registry))
    # `_save_registry` also reconciles into the `app` table; skip that path
    # entirely rather than asserting the effect of tenancy the test never
    # configures (services.apps.reconcile_from_registries already no-ops
    # without one, but doing it explicitly keeps the fixture from depending on
    # that fallback staying true).
    from services import apps as apps_svc
    monkeypatch.setattr(apps_svc, "reconcile_from_registries",
                        lambda: {"ok": False, "reason": "test"})
    return registry, proj_dir


def _bind_repo_stub(remote_url="https://github.com/acme/widgets.git",
                    default_branch="main"):
    def _fake(dir, remote_url_arg):
        return {"owner": "acme", "name": "widgets", "remote_url": remote_url,
                "default_branch": default_branch}
    return _fake


def _registry_repo(registry) -> dict:
    return json.loads(registry.read_text())["projects"]["widgets"].get("repo") or {}


# ---- the MCP tool writes the registry record --------------------------------

def _call(name: str, args: dict | None = None) -> str:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})
    assert resp is not None
    return resp["result"]["content"][0]["text"]


def _listed() -> set[str]:
    resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"] for t in resp["result"]["tools"]}


def test_offered_only_inside_a_project_chat(monkeypatch):
    monkeypatch.delenv("WORKSHOP_QUERY_SCOPE", raising=False)
    assert "set_repo" not in _listed()
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")
    assert "set_repo" not in _listed()
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "project:widgets")
    assert "set_repo" in _listed()


def test_the_tool_writes_the_registry_record(seeded, monkeypatch):
    registry, proj_dir = seeded
    from services import github

    monkeypatch.setattr(github, "bind_repo", _bind_repo_stub())
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "project:widgets")

    assert _registry_repo(registry) == {}
    out = _call("set_repo", {"remote_url": "https://github.com/acme/widgets.git"})
    assert "Bound widgets's repo" in out
    assert "https://github.com/acme/widgets.git" in out

    repo = _registry_repo(registry)
    assert repo["remote_url"] == "https://github.com/acme/widgets.git"
    assert repo["default_branch"] == "main"
    assert repo["owner"] == "acme"
    assert repo["provider"] == "github"


def test_default_branch_override_is_recorded(seeded, monkeypatch):
    registry, proj_dir = seeded
    from services import github

    monkeypatch.setattr(github, "bind_repo", _bind_repo_stub(default_branch="main"))
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "project:widgets")

    _call("set_repo", {"remote_url": "https://github.com/acme/widgets.git",
                       "default_branch": "trunk"})
    assert _registry_repo(registry)["default_branch"] == "trunk"


def test_without_a_project_scope_it_refuses_rather_than_writing(seeded, monkeypatch):
    registry, proj_dir = seeded
    from services import github

    monkeypatch.setattr(github, "bind_repo", _bind_repo_stub())
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "workshop")

    # Dispatch directly (bypassing the tools/list gate) -- the handler itself
    # must refuse, not only the advertisement.
    out = _call("set_repo", {"remote_url": "https://github.com/acme/widgets.git"})
    assert "not bound to a project" in out
    assert _registry_repo(registry) == {}


def test_a_blocked_owner_is_refused_before_any_bind_runs(seeded, monkeypatch):
    registry, proj_dir = seeded
    from services import github, installation

    # A FIXTURE NAME, NOT A REAL ORGANISATION. This test asserts the blocked-owner
    # MECHANISM, which reads the env var, so any value exercises it equally. It
    # originally named the operator's real work organisation, which put that name
    # into tracked source and tripped check_no_hardcoding. The standing rule is
    # that this repository and that organisation are never mixed, and a test
    # fixture is not an exception to it.
    blocked = "blocked-org-fixture"
    monkeypatch.setenv(installation.BLOCKED_OWNERS_ENV, blocked)

    def _must_not_run(dir, remote_url):
        pytest.fail("bind_repo ran for a blocked owner")

    monkeypatch.setattr(github, "bind_repo", _must_not_run)
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "project:widgets")

    out = _call("set_repo", {"remote_url": f"https://forge.invalid/{blocked}/secret.git"})
    assert "error" in out.lower()
    assert blocked in out
    assert _registry_repo(registry) == {}


# ---- the same write, reachable as the HTTP route ----------------------------

def test_the_http_route_goes_through_the_same_write(seeded, monkeypatch):
    """`PATCH /projects/{slug}` and the MCP tool must not diverge: both call
    `routes.projects.set_repo`, so this asserts the route's own call reaches
    the identical registry shape the tool test above checked."""
    import asyncio

    from routes import projects as projects_routes

    registry, proj_dir = seeded
    monkeypatch.setattr("services.github.bind_repo", _bind_repo_stub())

    body = projects_routes.ProjectPatchIn(remote_url="https://github.com/acme/widgets.git",
                                          default_branch="trunk")
    result = asyncio.run(projects_routes.patch_project("widgets", body))
    assert result.repo.remote_url == "https://github.com/acme/widgets.git"
    assert result.repo.default_branch == "trunk"
    assert _registry_repo(registry)["remote_url"] == "https://github.com/acme/widgets.git"


def test_default_branch_alone_is_refused_over_http(seeded):
    import asyncio

    from fastapi import HTTPException

    from routes import projects as projects_routes

    body = projects_routes.ProjectPatchIn(default_branch="trunk")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(projects_routes.patch_project("widgets", body))
    assert exc.value.status_code == 400
