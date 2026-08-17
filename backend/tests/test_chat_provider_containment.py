"""`get_provider` must never build a project/build provider whose cwd escaped its
intended root.

Found 2026-08-11 auditing why the `general` scope could reach the platform
repo: `project:<slug>` had NO validation between the raw HTTP `context_key` and
`ProjectContextProvider`'s own `_projects_root() / slug` join, and
`build:<slug>` defaulted an unknown slug's cwd to the platform repo outright.
Either gave a chat full agentic tools (bypassPermissions, no --allowedTools) at
a cwd nothing had vetted. See docs/security-audit-2026-08-11-scope-separation.md
section 2.
"""
import importlib


import pytest


@pytest.fixture
def ctx():
    """Clean reload so module-level state (e.g. the general provider's tool
    list) is not shared with whatever ran before this test."""
    from services import chat_context
    importlib.reload(chat_context)
    return chat_context


class TestProjectSlugTraversal:
    def test_a_traversal_slug_never_becomes_a_project_provider(self, ctx):
        for payload in ("../../..", "..%2F..%2F..", "/etc", "..", "a/../../b"):
            p = ctx.get_provider(f"project:{payload}")
            assert p.key != f"project:{payload}", (
                f"project:{payload!r} resolved to a real ProjectContextProvider"
            )
            # Falls all the way through to the tool-less general provider, not
            # some other build-capable one.
            assert not ctx.is_build_provider(p)

    def test_an_ordinary_project_slug_still_resolves(self, ctx):
        from tests.conftest import requires_registry
        data = requires_registry()
        slug = next(iter((data.get("projects") or {}).keys()), None)
        if slug is None:
            pytest.skip("no project registered on this instance")
        p = ctx.get_provider(f"project:{slug}")
        assert p.key == f"project:{slug}"
        assert ctx.is_build_provider(p)


    def test_the_constructor_itself_refuses_to_leave_projects(self, ctx):
        """Defense in depth, and a DIFFERENT check from the slug regex above.

        `get_provider` screens the key; this proves the PATH. The two do not
        subsume each other — a regex does not see a symlink, and containment
        does not care what the key looks like — and every caller other than
        `get_provider` only ever gets this one.
        """
        for payload in ("../../..", "..", "a/../../b"):
            with pytest.raises(ctx.ProjectOutsideRoot):
                ctx.ProjectContextProvider(payload)

    def test_a_symlink_out_of_projects_is_caught_too(self, ctx, tmp_path, monkeypatch):
        root = tmp_path / "projects"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (root / "escape").symlink_to(outside)
        monkeypatch.setattr(ctx, "_projects_root", lambda: root)
        with pytest.raises(ctx.ProjectOutsideRoot):
            ctx.contained_project_dir("escape")


class TestBuildAgentUnknownSlug:
    def test_an_unregistered_slug_never_gets_the_platform_repo(self, ctx, monkeypatch):
        from services import paths
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")  # the exact condition that used to let this through
        p = ctx.get_provider("agent:this-agent-does-not-exist-xyz")
        assert p.key != "agent:this-agent-does-not-exist-xyz"
        assert not ctx.is_build_provider(p) or str(p.dir) != str(paths.workshop_root())

    def test_the_constructor_itself_refuses_an_unknown_slug(self):
        """Defense in depth: get_provider is the audited chokepoint, but the
        constructor must not silently hand back a real (wrong) cwd for anyone
        who calls it directly."""
        from services import chat_context
        with pytest.raises(KeyError):
            chat_context.MakerAgentContextProvider("this-build-agent-does-not-exist-xyz")
