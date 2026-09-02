"""The AppSpec extension points, exercised by an app the platform has never
heard of.

WHY THIS FILE EXISTS. Before these seams, the platform imported FROM apps in six
places, each hardcoding an app name: a `VERIFIERS = {"plaid": ...}` dict, an
`if source == "plaid":` branch, a `_ADAPTERS` tuple naming two apps' classes, a
`collect_vault()` that imported the notes app's module, a route module reaching
into another route module's privates to publish an event, and 560 lines of four
apps' business logic in a platform directory.

Each of those was "tolerant" -- a missing app degraded rather than crashing --
and tolerant is not the same as extensible. The test of extensible is whether
something the platform has never heard of can plug in. So every test here uses a
fabricated app (`seamtest`) with a fabricated source, type and corpus, and
asserts the platform picks it up WITHOUT any platform file naming it.
"""
from __future__ import annotations

import importlib

import pytest

from services.app_registry import AppSpec


def _registry():
    """The LIVE app_registry module, resolved per call.

    Not `from services import app_registry` at import time:
    tests/test_app_registry_tolerant.py pops the module out of sys.modules and
    re-imports it to prove the catalog survives a missing route module, so an
    import-time binding here would patch a stale module object while the code
    under test imports the fresh one. That is invisible when this file runs
    alone and fails only in a full-suite run, which is the worst way to find it.
    """
    return importlib.import_module("services.app_registry")


# --------------------------------------------------------------------------- #
# One fake app, used by every test below. It names nothing real.
# --------------------------------------------------------------------------- #
def _seam_verifier(headers, body):
    return (headers.get("x-seam-token") == "open", "seam_token")


def _seam_dedup(summary):
    got = (summary or {}).get("seam_id")
    return f"seamtest:{got}" if got else None


class _SeamAdapter:
    type = "SeamType"

    def claims(self, store):
        return (getattr(store, "type", None) or "") == "SeamType"

    def selectors(self):
        return ["query"]

    def resolve(self, store, path, selector, verbs, verb):
        return [{"seam": "row"}]

    def to_response(self, items, *, mode, filter_caveat):
        return [dict(i) for i in items]

    def write(self, store, path, content, *, verb, selector):
        return {"wrote": content}


def _seam_collector():
    return ["seam-chunk"]


SEAM_SPEC = AppSpec(
    id="seamtest",
    event_sources=("seamsource",),
    event_verifiers={"seamsource": _seam_verifier},
    event_dedup={"seamsource": _seam_dedup},
    store_adapters=(_SeamAdapter(),),
    index_collectors={"seamkind": _seam_collector},
)


# Some catalog tests need "an in-tree app that is not the fake app". Taken from
# the catalog rather than typed, so this file names no real app either.
#
# Read from the DISCOVERY scan since 2026-09-01: the registry used to hold a
# literal `_CATALOG_FACTORIES` tuple, and reading it here was the only way to
# get a real id without writing one down. The scan is now that source, and the
# id still comes from the spec each declaration returns, never from a filename.
def _a_builtin_id() -> str:
    ids = []
    for _source, factory in _registry()._in_tree_factories():
        try:
            ids.append(factory().id)
        except Exception:
            continue
    if not ids:
        pytest.skip("this instance ships no in-tree apps")
    return ids[0]


@pytest.fixture
def seam_installed(monkeypatch):
    """Install the fake app, by making it the entire installed set."""
    monkeypatch.setattr(_registry(), "installed_specs", lambda: [SEAM_SPEC])
    return SEAM_SPEC


# --------------------------------------------------------------------------- #
# Seam 1: webhook signature verification.
# --------------------------------------------------------------------------- #
class TestEventVerifierSeam:
    def test_an_installed_app_verifies_its_own_source(self, seam_installed):
        from routes import events

        v = events.verifier_for("seamsource")
        assert v({"x-seam-token": "open"}, b"")[0] is True
        assert v({"x-seam-token": "wrong"}, b"")[0] is False

    def test_an_uninstalled_apps_source_gets_the_permissive_default(self, monkeypatch):
        from routes import events

        monkeypatch.setattr(_registry(), "installed_specs", lambda: [])
        v = events.verifier_for("seamsource")
        assert v({}, b"") == (True, "no_verifier_registered")

    def test_a_platform_source_is_not_overridable_by_an_app(self, monkeypatch):
        """An app must not be able to claim `github` and vouch for it."""
        from routes import events

        hijack = AppSpec(id="seamtest", event_verifiers={"github": _seam_verifier})
        monkeypatch.setattr(_registry(), "installed_specs", lambda: [hijack])
        assert events.verifier_for("github") is events._verify_github


# --------------------------------------------------------------------------- #
# Seam 2: cross-arrival idempotency.
# --------------------------------------------------------------------------- #
class TestEventDedupSeam:
    def test_an_app_derives_its_own_key(self, seam_installed):
        from services import event_dispatch

        assert event_dispatch._idempotency_key("seamsource", {"seam_id": "abc"}) == "seamtest:abc"

    def test_an_apps_none_is_an_answer_not_a_fallthrough(self, seam_installed):
        """The platform's content hash must not overrule an owner that has said
        this payload carries no stable identity: it would dedup two genuinely
        distinct events that happened to serialize alike."""
        from services import event_dispatch

        assert event_dispatch._idempotency_key("seamsource", {"noise": 1}) is None

    def test_an_unclaimed_source_still_gets_the_content_hash(self, seam_installed):
        from services import event_dispatch

        key = event_dispatch._idempotency_key("someone-else", {"a": 1})
        assert key and key.startswith("someone-else:")


# --------------------------------------------------------------------------- #
# Seam 3: store adapters.
# --------------------------------------------------------------------------- #
class TestStoreAdapterSeam:
    def _store(self, type_):
        from services.stores import Store

        return Store(uri="workshop://store/seamtest/x", owner_app="seamtest",
                     name="x", type=type_, path="/tmp/seamtest")

    def test_an_installed_apps_type_resolves(self, seam_installed):
        from services import stores

        assert isinstance(stores.adapter_for(self._store("SeamType")), _SeamAdapter)

    def test_an_unclaimed_type_is_export_only(self, seam_installed):
        from services import stores

        with pytest.raises(stores.AccessDenied):
            stores.adapter_for(self._store("NoSuchType"))

    def test_the_platform_keeps_its_own_note_substrate(self, monkeypatch):
        """The knowledge store is the PLATFORM's, and must stay reachable with
        no apps installed at all."""
        from services import stores

        monkeypatch.setattr(_registry(), "installed_specs", lambda: [])
        s = stores.Store(uri="workshop://store/knowledge/workshop",
                         owner_app="knowledge", name="workshop", type="Note",
                         path="/tmp/k")
        assert isinstance(stores.adapter_for(s), stores.KnowledgeNoteAdapter)

    def test_a_store_is_unwritable_unless_its_adapter_says_otherwise(self):
        """The default is refusal. The write door used to hardcode which app it
        could write and reach into that app's module directly."""
        from services import stores

        base = stores.StoreAdapter()
        with pytest.raises(stores.AccessDenied):
            base.write(self._store("Whatever"), "", "body", verb="write", selector={})


# --------------------------------------------------------------------------- #
# Seam 4: semantic-index collectors.
# --------------------------------------------------------------------------- #
class TestIndexCollectorSeam:
    def test_an_installed_apps_corpus_becomes_a_kind(self, seam_installed):
        from services import semantic_index as si

        assert "seamkind" in si.kinds()
        assert si.collectors()["seamkind"]() == ["seam-chunk"]

    def test_a_corpus_is_opt_in_unless_declared_default(self, seam_installed):
        from services import semantic_index as si

        assert "seamkind" not in si.default_kinds()

    def test_declaring_it_default_puts_it_in_every_build(self, monkeypatch):
        from services import semantic_index as si

        spec = AppSpec(id="seamtest", index_collectors={"seamkind": _seam_collector},
                       index_collectors_default=("seamkind",))
        monkeypatch.setattr(_registry(), "installed_specs", lambda: [spec])
        assert "seamkind" in si.default_kinds()

    def test_the_platform_kinds_survive_with_no_apps(self, monkeypatch):
        from services import semantic_index as si

        monkeypatch.setattr(_registry(), "installed_specs", lambda: [])
        assert si.kinds() == si.PLATFORM_KINDS
        assert si.default_kinds() == si.PLATFORM_DEFAULT_KINDS

    def test_the_legacy_constant_names_still_resolve(self, seam_installed):
        """KINDS/DEFAULT_KINDS were module constants read by the MCP query
        server and the tests; they are derived now and still answer."""
        from services import semantic_index as si

        assert "seamkind" in si.KINDS
        assert "seamkind" not in si.DEFAULT_KINDS


# --------------------------------------------------------------------------- #
# Seam 5: publishing an event.
# --------------------------------------------------------------------------- #
class TestEventPublishSeam:
    def test_publish_records_the_offset_before_the_write(self, tmp_path, monkeypatch):
        """The offset is the event's address: a pending run seeks to it to
        re-read the payload. Off by one record and it reads the wrong event."""
        import json

        from services import event_log

        logfile = tmp_path / "events.jsonl"
        monkeypatch.setattr(event_log, "EVENTS_LOG", logfile)
        first = event_log.publish("seamsource", {"n": 1}, known=True)
        second = event_log.publish("seamsource", {"n": 2}, known=True)
        assert first == 0
        raw = logfile.read_bytes()
        assert json.loads(raw[first:].split(b"\n")[0])["body"] == {"n": 1}
        assert json.loads(raw[second:].split(b"\n")[0])["body"] == {"n": 2}

    def test_credential_headers_never_reach_the_log(self, tmp_path, monkeypatch):
        import json

        from services import event_log

        logfile = tmp_path / "events.jsonl"
        monkeypatch.setattr(event_log, "EVENTS_LOG", logfile)
        event_log.publish(
            "seamsource",
            {},
            headers={"Authorization": "Bearer x", "Cookie": "s=1",
                     "X-Seam-Signature": "sig", "Accept": "application/json"},
            redact_headers=("x-seam-signature",),
            known=True,
        )
        headers = json.loads(logfile.read_text())["headers"]
        assert set(headers) == {"Accept"}

    def test_a_published_record_carries_the_signature_verdict(self, tmp_path, monkeypatch):
        """The plaid copy of this sequence omitted `signature` entirely, so a
        verified webhook was indistinguishable from an unverified one."""
        import json

        from services import event_log

        logfile = tmp_path / "events.jsonl"
        monkeypatch.setattr(event_log, "EVENTS_LOG", logfile)
        event_log.publish("seamsource", {}, signature={"ok": True, "reason": "seam"},
                          known=True)
        assert json.loads(logfile.read_text())["signature"] == {"ok": True, "reason": "seam"}


# --------------------------------------------------------------------------- #
# The catalog itself: can something outside this repo become an app?
# --------------------------------------------------------------------------- #
class TestOutOfTreeApps:
    def test_an_entry_point_app_enters_the_catalog(self, monkeypatch):
        """The real test of the seams. The catalog was a literal tuple
        consumed once at import, with the factory required to be defined in
        app_registry.py and the route module required to sit in backend/routes/,
        so nothing outside backend/ could be an app at all."""
        class _EP:
            name = "seamtest"
            value = "seamtest.spec:app_spec"

            def load(self):
                return lambda: SEAM_SPEC

        monkeypatch.setattr(_registry(), "_entry_point_factories",
                            lambda: [("seamtest", lambda: SEAM_SPEC)])
        catalog = _registry()._build_catalog()
        assert catalog["seamtest"] is SEAM_SPEC
        # and it did not displace the built-ins
        assert _a_builtin_id() in catalog

    def test_a_discovered_app_supersedes_a_built_in_of_the_same_id(self, monkeypatch):
        """What extraction looks like from the platform's side: the app ships as
        a distribution and takes over from the remnant still in the tree."""
        target = _a_builtin_id()
        replacement = AppSpec(id=target)
        monkeypatch.setattr(_registry(), "_entry_point_factories",
                            lambda: [(target, lambda: replacement)])
        assert _registry()._build_catalog()[target] is replacement

    def test_a_broken_entry_point_does_not_sink_the_catalog(self, monkeypatch):
        def _boom():
            raise RuntimeError("bad spec")

        monkeypatch.setattr(_registry(), "_entry_point_factories",
                            lambda: [("seamtest", _boom)])
        assert _a_builtin_id() in _registry()._build_catalog()

    def test_a_non_appspec_return_is_rejected(self, monkeypatch):
        """An entry point that returns a dict must fail loudly at build, not
        enter the catalog and fail later at every accessor that reads a field."""
        import importlib.metadata as md

        class _EP:
            name = "seamtest"
            value = "seamtest.spec:app_spec"

            def load(self):
                return lambda: {"id": "seamtest"}

        monkeypatch.setattr(md, "entry_points", lambda group=None: [_EP()])
        (_, factory), = _registry()._entry_point_factories()
        with pytest.raises(TypeError):
            factory()

    def test_such_an_app_is_skipped_rather_than_sinking_the_catalog(self, monkeypatch):
        import importlib.metadata as md

        class _EP:
            name = "seamtest"
            value = "seamtest.spec:app_spec"

            def load(self):
                return lambda: {"id": "seamtest"}

        monkeypatch.setattr(md, "entry_points", lambda group=None: [_EP()])
        catalog = _registry()._build_catalog()
        assert "seamtest" not in catalog
        assert _a_builtin_id() in catalog
