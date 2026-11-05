"""Where a chat's web links open (services/web_links)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import web_links

MANIFESTS = {
    "pages": {"services": [{"name": "app", "expose": True, "opens": {"web": "#open={url}&n={n}"}}]},
    "plain": {"services": [{"name": "web", "expose": True}]},
    "hidden": {"services": [{"name": "api", "expose": False, "opens": {"web": "#open={url}"}}]},
    "odd": {"services": [{"name": "app", "expose": True, "opens": {"web": "open={url}"}}]},
    "native": {"services": [{"name": "app", "expose": True,
                             "opens": {"web": "#open={url}", "native_pages": True}}]},
}


@pytest.fixture
def apps(monkeypatch):
    prefs: dict = {}
    monkeypatch.setattr(web_links.app_links, "all_app_slugs", lambda: list(MANIFESTS))
    monkeypatch.setattr(web_links.app_links, "linked_project_slug", lambda s: s)
    monkeypatch.setattr(web_links.app_links, "load", lambda: SimpleNamespace(projects={"pages": {"title": "Pages"}}))
    monkeypatch.setattr(web_links.app_runner, "is_self_project", lambda p: False)
    monkeypatch.setattr(web_links.app_runner, "_project_dir", lambda p: p)
    monkeypatch.setattr(web_links.app_runner, "_read_manifest_file", lambda root: MANIFESTS[root])
    monkeypatch.setattr(web_links.user_prefs, "read", lambda: dict(prefs))
    monkeypatch.setattr(web_links.user_prefs, "write",
                        lambda k, v: prefs.pop(k, None) if v is None else prefs.__setitem__(k, v))
    return prefs


def test_only_an_exposed_service_with_a_url_template_opens_web_links(apps):
    assert web_links.handlers() == [
        web_links.Handler(app="pages", label="Pages", route="/apps/pages/ui/app#open={url}&n={n}"),
        web_links.Handler(app="native", label="native", route="/apps/native/ui/app#open={url}",
                          native_pages=True)]


def test_the_choice_is_kept_and_a_new_window_is_the_default(apps):
    assert web_links.chosen() is None
    assert web_links.choose("pages").app == "pages"
    assert web_links.chosen().route.startswith("/apps/pages/ui/app")
    with pytest.raises(ValueError):
        web_links.choose("plain")
    assert web_links.choose(None) is None and web_links.chosen() is None
