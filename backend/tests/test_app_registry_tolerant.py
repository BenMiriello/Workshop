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


class _BlockModules:
    """An import meta-path finder that makes specific modules look unshipped
    (raise ModuleNotFoundError), simulating an instance that omits them."""

    def __init__(self, blocked: set[str]):
        self.blocked = set(blocked)

    def find_spec(self, name, path=None, target=None):
        if name in self.blocked:
            raise ModuleNotFoundError(f"blocked for test: {name}")
        return None  # defer to the normal finders


# THIS FILE NAMES NO APP, and that is part of what it tests. It used to type
# seven app ids and three route-module names into its assertions, so the gate
# that measures "no platform source names an app" counted 28 sites here and the
# test would have to be edited every time the operator added or removed an app.
# Everything below is derived from the catalog's own discovery scan instead.
#
# IT MUST ASK BOTH SOURCES, and on 2026-09-04 it did not. It read only
# `_in_tree_factories()`, which returned every app until the day every app left
# the repo -- and then returned nothing, so this file's central assertion became
# `assert []` and the guard that says "an app declared here reached the catalog"
# stopped guarding anything. That is the same failure the app-name gate's roster
# had on the same day, twice, and it is worth naming the shape: a discovery
# helper that reads one place goes silent rather than loud when the thing it
# reads about moves.
def _declarations():
    """(blockable module name, spec) for every declaration on this instance.

    The first element is a module whose absence must drop THIS app and no other.
    In-tree that is the declaration module; out-of-tree it is the entry point's
    own module, whose absence is what an uninstalled distribution looks like.
    """
    ar = _reload_registry()
    out = []
    for source, factory in ar._in_tree_factories():
        try:
            out.append((f"services.{source}", factory()))
        except Exception:
            continue
    for name, mod in _entry_point_modules().items():
        spec = _spec_from_entry_point(ar, name)
        if spec is not None:
            out.append((mod, spec))
    return out


def _entry_point_modules() -> dict[str, str]:
    """{entry point name: module it loads from}, for the app entry-point group.

    Read from importlib.metadata rather than from the registry, so a change in
    how the registry wraps its factories cannot quietly empty this too.
    """
    from importlib.metadata import entry_points

    import services.app_registry as ar
    out = {}
    for ep in entry_points(group=ar.APP_ENTRY_POINT_GROUP):
        out[ep.name] = ep.value.split(":")[0]
    return out


def _spec_from_entry_point(ar, name):
    for source, factory in ar._entry_point_factories():
        if source != name:
            continue
        try:
            return factory()
        except Exception:
            return None
    return None


def test_every_declared_app_reaches_the_catalog():
    """THE GUARD. `_build_catalog` logs and SKIPS a declaration that raises, so a
    broken app does not error -- it is simply absent, with one INFO line nobody
    reads. Two rename mistakes during the 2026-09-04 extraction were caught only
    by this property, and an app's own tests cannot catch it: they import the app
    directly and never ask the platform whether it was discovered."""
    ar = _reload_registry()
    declared = _declarations()
    assert declared, (
        "no app declarations discovered from EITHER source: neither "
        "`services/*_app.py` (in-tree) nor the `workshop.apps` entry-point group. "
        "If this instance genuinely has no apps then every assertion below is "
        "vacuous -- which is exactly how this file passed while reading only the "
        "in-tree source, on the day every app left the tree."
    )
    for _mod, spec in declared:
        assert spec.id in ar.BUILTIN_APPS, f"{spec.id} declared but missing from the catalog"


def test_the_catalog_is_exactly_what_declared_itself():
    """Nothing in the catalog that nothing declared, and nothing declared lost."""
    ar = _reload_registry()
    declared_ids = {spec.id for _m, spec in _declarations()}
    assert declared_ids == set(ar.BUILTIN_APPS), (
        f"declared {sorted(declared_ids)} but catalog holds {sorted(ar.BUILTIN_APPS)}"
    )


def test_missing_module_drops_only_its_app_and_never_crashes():
    """The regression guard for the 2026-07-12 crash: `app_registry` used to hard
    import every app's route module, so one absent module took the whole platform
    down. A slimmed instance must lose exactly the app whose code is missing.

    REWRITTEN 2026-09-04. It used to block `routes.<name>` for an in-tree app's
    declared REQUIRES, and after extraction no in-tree app existed, so it skipped
    -- silently, for the reason it was written to test. An out-of-tree app's
    "module not shipped" is its distribution not being installed, which is the
    entry point's own module failing to import, so that is what is blocked now.
    Same property, addressed at whichever layer the app actually lives in.
    """
    declared = _declarations()
    if len(declared) < 2:
        pytest.skip("needs at least two apps to show that only ONE is dropped")
    (victim_mod, victim), *others = declared

    blocker = _BlockModules({victim_mod})
    # Ensure it is not already cached, so the finder is consulted at all.
    for cached in [n for n in sys.modules if n == victim_mod or n.startswith(victim_mod + ".")]:
        sys.modules.pop(cached, None)
    sys.meta_path.insert(0, blocker)
    try:
        ar = _reload_registry()  # must NOT raise
        assert victim.id not in ar.BUILTIN_APPS, \
            f"{victim.id} should be dropped when its module is absent"
        for mod, spec in others:
            if mod == victim_mod or mod.startswith(victim_mod + "."):
                continue  # shares the blocked module; legitimately dropped too
            assert spec.id in ar.BUILTIN_APPS, f"{spec.id} dropped by an unrelated absence"
        # A dropped app can't be installed/mounted/listed even if named in the roster.
        assert all(s.id != victim.id for s in ar.installed_specs())
    finally:
        sys.meta_path.remove(blocker)
        for cached in [n for n in sys.modules if n == victim_mod or n.startswith(victim_mod + ".")]:
            sys.modules.pop(cached, None)
        _reload_registry()  # restore a full catalog for any later test


def test_chat_personas_are_install_gated():
    """A chat persona is available only when its app is installed — chat_context
    assembles its provider map from installed apps, hardcoding none.

    The app ids come from the catalog, never typed: which apps ship a persona is
    exactly the kind of fact a platform test must not carry.
    """
    from services import chat_context

    from tests.conftest import requires_app

    # Ids FIRST: `_declarations()` reloads the registry, so a binding taken
    # before it would patch the popped module object while chat_context reads
    # the fresh one -- the exact stale-module trap this file's reload helper
    # sets, and it fails as a persona that "leaked".
    with_persona = [s.id for _m, s in _declarations() if s.chat_provider]
    app_registry = importlib.import_module("services.app_registry")
    if len(with_persona) < 2:
        pytest.skip("this instance ships fewer than two apps with a chat persona")

    # The full roster is only "full" where those apps are installed. On a fresh
    # instance none are, and asserting them here failed for the absence of a
    # feature rather than for the behaviour under test.
    for key in with_persona:
        requires_app(key)

    full = chat_context.provider_map()
    for key in ("general", *with_persona):
        assert key in full, f"{key} persona missing from full provider map"

    # Simulate an instance with only ONE of them installed: only its persona (+
    # platform providers) should appear; every other app persona must be absent.
    kept, *dropped = with_persona
    orig = app_registry.installed_specs
    app_registry.installed_specs = lambda: [s for s in orig() if s.id == kept]
    try:
        slim = chat_context.provider_map()
        assert kept in slim
        assert "general" in slim  # platform provider always present
        for gone in dropped:
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


def test_every_card_route_is_the_apps_own_id():
    """A card's route is where the platform SENDS you, so it has to be a route
    that exists.

    `app_surface.surface_for` takes the built-in card's `route` as the app's
    home, and the frontend registers each app's page at `/apps/<id>`
    (`frontend/src/apps/registry.tsx` builds the path from the id). So a card
    whose route is anything else points the reader at a route nobody serves:
    the shell finds no app with that slug and bounces to Home with an error.

    Measured 2026-09-01: one app's card declared a route that dropped a suffix
    from its own id, and that app's own test asserted the wrong literal, which
    pinned the bug rather than catching it. Opening the app went to Home with an
    error. This guard is written over the whole catalog, and names no app, so it
    covers the next one too.

    A card with NO route is fine and not what this guards: that app simply has
    no screen of its own and takes the platform's tabs.
    """
    from services import app_registry

    wrong = {
        app_id: spec.card["route"]
        for app_id, spec in app_registry.BUILTIN_APPS.items()
        if (spec.card or {}).get("route")
        and spec.card["route"] != f"/apps/{app_id}"
    }
    assert not wrong, (
        "these apps declare a route that is not their own id, so opening them "
        f"lands on a route nobody serves: {wrong}"
    )
