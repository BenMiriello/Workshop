"""The platform repo is a chat scope named `development`, in developer mode only.

Dev mode was on in all three places (backend env, Vite, the built bundle) and the
Development group still had no rows in the scope picker. The picker filters the
BACKEND's provider list by group, and the only platform provider was `general`.
Phase 3 renamed the `self: true` registry row to `development`, but the chat
layer still built it through ProjectContextProvider, which stamps `project:` on
the front, so the scope resolved as `project:development` and read as an
ordinary project.

Two properties matter and both are asserted here: the scope EXISTS as a
top-level key when dev mode is on, and the platform repo appears exactly ONCE.
A row under both Development and Projects would be the alias multiplication
ADR 0014 exists to remove.
"""
from pathlib import Path

import pytest


@pytest.fixture
def ctx(monkeypatch):
    """The module itself. DO NOT `importlib.reload` it here.

    `list_providers()` goes through `provider_map()`, which is rebuilt per call
    and reads `dev_mode.dev_mode_on()` per call precisely so the flag is not
    frozen at import — so these tests never needed a fresh import, and
    `monkeypatch.setenv` is enough.

    A reload rebinds the module's classes to new objects while already-imported
    modules keep the old ones, which breaks every `isinstance` check against
    them process-wide. See the fixture in
    `tests/test_chat_provider_containment.py` for the seven worktree tests that
    cost, and why they only failed in the full suite.
    """
    from services import chat_context
    return chat_context


def _keys(ctx):
    return [p["key"] for p in ctx.list_providers()]


class TestDeveloperModeOn:
    def test_development_is_offered(self, ctx, monkeypatch):
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        assert "development" in _keys(ctx)

    def test_the_platform_repo_is_not_also_a_project(self, ctx, monkeypatch):
        """The self row must not appear a second time under Projects."""
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        keys = _keys(ctx)
        selves = [k for k in keys if k.startswith("project:") and "development" in k]
        assert selves == [], f"platform repo listed twice: {selves}"

    def test_it_resolves_to_the_platform_repo(self, ctx, monkeypatch):
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        # Needs the registry row that points the development scope at the repo.
        # Without it the provider falls back to <root>/projects/development,
        # which is a real rough edge on a fresh developer clone but is not what
        # this test is about.
        from tests.conftest import requires_registry
        requires_registry()
        p = ctx.get_provider("development")
        assert p.key == "development"
        # Compare against the derived root, not against the directory NAME. The
        # old assertion fell through to `p.dir.name == "workshop"`, which is only
        # true when the checkout happens to be in a directory called workshop.
        from wsplatform import paths
        assert Path(p.dir).resolve() == paths.workshop_root()

    def test_it_is_a_build_provider(self, ctx, monkeypatch):
        """Working on the platform is a build chat: full agentic toolset, cwd
        pinned. Empty allowed_tools is how routes/chat.py recognises that.

        This used to also assert `p.role == "builder"`, which was the retired
        assistant/builder axis restating what the line above already says. That
        field was deleted on 2026-08-17 (it was serialised to a client that
        never read it, and its comment claimed it drove the label and the model
        while both are their own fields). The model tier is asserted instead,
        because that IS a real difference between a build chat and a data one."""
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        from services import llm_router
        p = ctx.get_provider("development")
        assert p.allowed_tools == []
        assert p.default_model == llm_router.TIER_SONNET


class TestDeveloperModeOff:
    def test_development_is_absent(self, ctx, monkeypatch):
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        assert "development" not in _keys(ctx)

    def test_a_bound_chat_does_not_silently_lose_its_tools(self, ctx, monkeypatch):
        """get_provider falls back to `general` for an unknown key. For this key
        that fallback would turn a chat pinned to the platform repo into a
        tool-less data chat without saying so, so the absence must be visible as
        a different provider rather than a quietly degraded one."""
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        p = ctx.get_provider("development")
        assert p.key != "development"

    def test_only_the_flag_string_1_counts(self, ctx, monkeypatch):
        """A stray "0" or "false" must not read as on."""
        for value in ("0", "false", "", "yes"):
            monkeypatch.setenv("WORKSHOP_DEV_MODE", value)
            assert "development" not in _keys(ctx), f"{value!r} read as on"


class TestExactlyOneNameForTheRepo:
    """ADR 0014 §4: one key resolves to the platform repo, and it is
    `development`.

    The registry row is ALSO named `development`, so `project:development`
    resolved to the repo too and it had two working addresses again. Only the
    dev-mode-OFF case was tested, which is why it survived the audit that added
    the gate.
    """

    def test_the_alias_does_not_resolve_even_in_dev_mode(self, monkeypatch):
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        from services import task_scope
        assert task_scope.root_for_context("project:development") is None

    def test_the_real_key_still_resolves_in_dev_mode(self, monkeypatch):
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        from services import task_scope
        assert task_scope.root_for_context("development") == task_scope.WORKSHOP_ROOT

    def test_neither_resolves_with_dev_mode_off(self, monkeypatch):
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        from services import task_scope
        assert task_scope.root_for_context("development") is None
        assert task_scope.root_for_context("project:development") is None

    def test_an_ordinary_project_is_unaffected(self, monkeypatch):
        """The gate must not swallow real projects."""
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        from services import task_scope
        from tests.conftest import requires_registry
        # Any real project will do: the point is that the gate does not
        # swallow ordinary projects, not that a particular one is present.
        data = requires_registry()
        slug = next((s for s in (data.get("projects") or {})
                     if s not in ("development",)), None)
        if slug is None:
            pytest.skip("no ordinary (non-development) project registered here")
        root = task_scope.root_for_context(f"project:{slug}")
        assert root is not None and root.name == slug


class TestTheDowngradeIsNotSilent:
    """`get_provider` degrades `development` to `general` when the flag is off,
    because it is total by contract and 24 call sites rely on that. The
    honesty is therefore owed at the seam: `dev_scope_refused` is what the
    turn path asks before answering, so a chat bound to the platform repo
    cannot quietly become a tool-less general chat and reply anyway."""

    def test_it_reports_a_refusal_when_the_flag_is_off(self, ctx, monkeypatch):
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        assert ctx.dev_scope_refused("development") is True

    def test_it_reports_nothing_when_the_flag_is_on(self, ctx, monkeypatch):
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        assert ctx.dev_scope_refused("development") is False

    def test_an_ordinary_scope_is_never_refused(self, ctx, monkeypatch):
        monkeypatch.delenv("WORKSHOP_DEV_MODE", raising=False)
        for key in ("general", "project:atlas", "ledger", None):
            assert ctx.dev_scope_refused(key) is False
