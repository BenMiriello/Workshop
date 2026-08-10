"""The built-in app catalog must degrade gracefully on a slimmed instance.

Workshop is a container: an instance may not ship every app's route module (a
slimmed/downstream build). The catalog (services/app_registry) is imported by
base_apps/main/llm_router, so if it hard-imported a missing app module the whole
platform would crash. These tests pin the tolerant behavior: a missing module
drops only its app, and importing the catalog never raises.

Regression guard for the 2026-07-12 crash where app_registry hard-imported every
app route module. See docs/apps-are-pluggable.md.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_registry():
    sys.modules.pop("services.app_registry", None)
    return importlib.import_module("services.app_registry")


class _BlockRouteModules:
    """An import meta-path finder that makes specific `routes.<name>` modules look
    unshipped (raise ModuleNotFoundError), simulating an instance that omits them."""

    def __init__(self, blocked: set[str]):
        self.blocked = {f"routes.{n}" for n in blocked}

    def find_spec(self, name, path=None, target=None):
        if name in self.blocked:
            raise ModuleNotFoundError(f"blocked for test: {name}")
        return None  # defer to the normal finders


def test_full_instance_has_all_builtin_apps():
    ar = _reload_registry()
    for app_id in ("finance", "budget", "commute", "location", "birds", "health", "wearables"):
        assert app_id in ar.BUILTIN_APPS, f"{app_id} missing from full catalog"


def test_missing_module_drops_only_its_app_and_never_crashes():
    blocker = _BlockRouteModules({"birds", "commute"})
    # Ensure the blocked modules aren't already cached, so the finder is consulted.
    for n in ("routes.birds", "routes.commute", "routes.environment"):
        sys.modules.pop(n, None)
    sys.meta_path.insert(0, blocker)
    try:
        ar = _reload_registry()  # must NOT raise
        assert "birds" not in ar.BUILTIN_APPS, "birds should be dropped when its module is absent"
        assert "commute" not in ar.BUILTIN_APPS, "commute should be dropped when its module is absent"
        # Unaffected apps stay; external app (no module requirement) always stays.
        assert "finance" in ar.BUILTIN_APPS
        assert "budget" in ar.BUILTIN_APPS
        assert "wearables" in ar.BUILTIN_APPS
        # A dropped app can't be installed/mounted/listed even if named in the roster.
        assert all(s.id != "birds" for s in ar.installed_specs())
    finally:
        sys.meta_path.remove(blocker)
        for n in ("routes.birds", "routes.commute", "routes.environment"):
            sys.modules.pop(n, None)
        _reload_registry()  # restore a full catalog for any later test


def test_chat_personas_are_install_gated():
    """A chat persona is available only when its app is installed — chat_context
    assembles its provider map from installed apps, hardcoding none."""
    from services import chat_context, app_registry

    # Full roster: the four app personas plus the general platform provider.
    full = chat_context.provider_map()
    for key in ("general", "finance", "budget", "commute", "location"):
        assert key in full, f"{key} persona missing from full provider map"

    # Simulate an instance with only 'budget' installed: only its persona (+
    # platform providers) should appear; finance/commute/location must be absent.
    orig = app_registry.installed_specs
    app_registry.installed_specs = lambda: [s for s in orig() if s.id == "budget"]
    try:
        slim = chat_context.provider_map()
        assert "budget" in slim
        assert "general" in slim  # platform provider always present
        for gone in ("finance", "commute", "location"):
            assert gone not in slim, f"{gone} persona leaked with its app uninstalled"
    finally:
        app_registry.installed_specs = orig


def test_chat_context_has_no_hardcoded_app_personas_or_operator():
    """chat_context is platform plumbing: no app persona classes, no hardcoded
    operator identity (the exact coupling the 2026-07-12 pass removed)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "services" / "chat_context.py").read_text()
    for banned in ("class BudgetContextProvider", "class FinanceContextProvider",
                   "class CommuteContextProvider", "class LocationContextProvider"):
        assert banned not in src, f"chat_context still defines {banned!r}"
    # Assert the MECHANISM, not a name: a test that greps for one operator's
    # name is the same hardcoding it is trying to forbid, and says nothing
    # about a fork. scripts/check_no_hardcoding.sh covers the literal case by
    # asking the machine who it is.
    assert "operator_name" in src, "chat_context no longer resolves the operator from config"


def test_catalog_never_hard_imports_app_modules_at_toplevel():
    """The source itself must not top-level `from routes import <app>` — that is
    exactly what reintroduces the crash. (Belt-and-suspenders to the shell guard.)"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "services" / "app_registry.py").read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("from routes import") and line[:1] not in (" ", "\t"):
            pytest.fail(f"app_registry top-level imports a route module: {stripped!r}")
