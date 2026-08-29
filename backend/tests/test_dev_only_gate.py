"""Development app is excluded from the catalog when dev mode is off.

Acceptance criterion for ticket 2d6e7597366e4541ab2e52e05aae43dd.

These tests exercise the service layer with the database patched out so no
real Postgres connection is required.  The filtering logic in
`services/apps.py` is what is under test: given a repo that returns a mix
of dev_only and non-dev_only rows, the service must hide the dev_only ones
when WORKSHOP_DEV_MODE is not '1'.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import apps as apps_svc
from wsplatform import dev_mode

_ORG = uuid.uuid4()

_DEV_APP = {
    "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
    "slug": "development",
    "name": "Workshop (development)",
    "icon": None,
    "kind": "app",
    "dev_only": True,
    "parent_id": None,
    "project_slug": "development",
    "archived_at": None,
    "created_at": None,
    "updated_at": None,
}

_BIRDS_APP = {
    "id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
    "slug": "birds",
    "name": "Birds",
    "icon": None,
    "kind": "app",
    "dev_only": False,
    "parent_id": None,
    "project_slug": "birds",
    "archived_at": None,
    "created_at": None,
    "updated_at": None,
}


@pytest.fixture(autouse=True)
def dev_mode_off(monkeypatch):
    monkeypatch.delenv(dev_mode.DEV_MODE_ENV, raising=False)
    assert not dev_mode.dev_mode_on()


@pytest.fixture
def dev_on(monkeypatch):
    monkeypatch.setenv(dev_mode.DEV_MODE_ENV, "1")
    assert dev_mode.dev_mode_on()


def _stub_tenancy(monkeypatch):
    """Patch tenancy and db.engine so services.apps._require_db() succeeds."""
    monkeypatch.setattr("services.tenancy.configured", lambda: True)
    monkeypatch.setattr("services.tenancy.available", lambda: True)
    monkeypatch.setattr("services.tenancy.org_and_user",
                        lambda: (_ORG, uuid.uuid4()))
    session = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("db.engine.tenant_session", lambda _: cm)
    return session


class TestDevOnlyCatalogGate:
    def test_dev_only_absent_from_list_when_dev_mode_off(self, monkeypatch):
        _stub_tenancy(monkeypatch)
        all_rows = [_DEV_APP, _BIRDS_APP]

        def filtered_list(s, org, *, dev_only=None, **kw):
            if dev_only is False:
                return [r for r in all_rows if not r["dev_only"]]
            return all_rows

        monkeypatch.setattr("db.app_repo.list_apps", filtered_list)
        result = apps_svc.list_apps()
        slugs = [r["slug"] for r in result]
        assert "development" not in slugs, \
            "dev_only app must be hidden when dev mode is off"
        assert "birds" in slugs, "non-dev-only app must still be visible"

    def test_dev_only_visible_when_dev_mode_on(self, monkeypatch, dev_on):
        _stub_tenancy(monkeypatch)
        all_rows = [_DEV_APP, _BIRDS_APP]

        def filtered_list(s, org, *, dev_only=None, **kw):
            if dev_only is False:
                return [r for r in all_rows if not r["dev_only"]]
            return all_rows

        monkeypatch.setattr("db.app_repo.list_apps", filtered_list)
        result = apps_svc.list_apps()
        slugs = [r["slug"] for r in result]
        assert "development" in slugs, \
            "dev_only app must be visible when dev mode is on"

    def test_get_app_returns_none_for_dev_only_when_dev_mode_off(self, monkeypatch):
        _stub_tenancy(monkeypatch)
        dev_id = str(_DEV_APP["id"])
        monkeypatch.setattr("db.app_repo.get_app",
                            lambda s, org, id: _DEV_APP if str(id) == dev_id else None)
        assert apps_svc.get_app(dev_id) is None, \
            "get_app must return None for dev_only app when dev mode is off"

    def test_get_app_visible_when_dev_mode_on(self, monkeypatch, dev_on):
        _stub_tenancy(monkeypatch)
        dev_id = str(_DEV_APP["id"])
        monkeypatch.setattr("db.app_repo.get_app",
                            lambda s, org, id: _DEV_APP if str(id) == dev_id else None)
        result = apps_svc.get_app(dev_id)
        assert result is not None and result["slug"] == "development", \
            "get_app must return the dev_only app when dev mode is on"

    def test_get_app_by_slug_returns_none_for_dev_only_when_dev_mode_off(
            self, monkeypatch):
        _stub_tenancy(monkeypatch)
        monkeypatch.setattr(
            "db.app_repo.get_app_by_slug",
            lambda s, org, slug: _DEV_APP if slug == "development" else None)
        assert apps_svc.get_app_by_slug("development") is None, \
            "get_app_by_slug must return None for dev_only app when dev mode is off"

    def test_get_app_by_slug_visible_when_dev_mode_on(self, monkeypatch, dev_on):
        _stub_tenancy(monkeypatch)
        monkeypatch.setattr(
            "db.app_repo.get_app_by_slug",
            lambda s, org, slug: _DEV_APP if slug == "development" else None)
        result = apps_svc.get_app_by_slug("development")
        assert result is not None and result["slug"] == "development", \
            "get_app_by_slug must return dev_only app when dev mode is on"

    def test_list_apps_passes_dev_only_false_to_repo_when_dev_mode_off(
            self, monkeypatch):
        """The repo call itself is filtered — not just the results."""
        _stub_tenancy(monkeypatch)
        calls: list[dict] = []

        def recording_list(s, org, **kw):
            calls.append(kw)
            return []

        monkeypatch.setattr("db.app_repo.list_apps", recording_list)
        apps_svc.list_apps()
        assert calls, "app_repo.list_apps must have been called"
        assert calls[0].get("dev_only") is False, \
            "dev_only=False must be passed to the repo when dev mode is off"

    def test_list_apps_passes_no_dev_only_filter_when_dev_mode_on(
            self, monkeypatch, dev_on):
        """With dev mode on the repo is called without a dev_only filter."""
        _stub_tenancy(monkeypatch)
        calls: list[dict] = []

        def recording_list(s, org, **kw):
            calls.append(kw)
            return []

        monkeypatch.setattr("db.app_repo.list_apps", recording_list)
        apps_svc.list_apps()
        assert calls, "app_repo.list_apps must have been called"
        assert calls[0].get("dev_only") is None, \
            "no dev_only filter must be passed to the repo when dev mode is on"
