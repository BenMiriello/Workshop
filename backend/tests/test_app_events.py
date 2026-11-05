"""Workshop events reach the running apps that subscribed to them.

Pinned: the manifest's `events` block parses tolerantly against the one
vocabulary; only a RUNNING subscribed service is called, at its current port
and declared path, with {event, at, ...fields}; a failing app costs nothing;
the route validates against the vocabulary and answers without waiting.
"""
from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import app_events as app_events_routes
from services import app_events, app_runner, ownership

HOOK = {"copied": {"service": "api", "path": "/hooks/copied"}}


def test_subscriptions_parse_known_events_and_skip_the_rest():
    raw = {"events": {
        "copied": {"service": "api", "path": "hooks/copied"},
        "unknown": {"service": "api", "path": "/x"},
    }}
    assert app_events.subscriptions(raw) == {"copied": ("api", "/hooks/copied")}


@pytest.mark.parametrize("raw", [
    None, {}, {"events": []}, {"events": {"copied": "api"}},
    {"events": {"copied": {"service": "api"}}},
    {"events": {"copied": {"path": "/h"}}},
])
def test_a_malformed_block_subscribes_to_nothing(raw):
    assert app_events.subscriptions(raw) == {}


@pytest.fixture()
def apps(monkeypatch):
    """Three known apps: one subscribed and running, one subscribed and
    stopped, one running without a subscription."""
    manifests = {"on": {"events": HOOK}, "off": {"events": HOOK}, "deaf": {}}
    targets = {"on": ("running", 8811), "off": ("stopped", None), "deaf": ("running", 8812)}
    monkeypatch.setattr(app_runner, "_STATE", {k: {} for k in manifests})
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: slug)
    monkeypatch.setattr(app_runner, "_read_manifest_file", lambda d: manifests[d])
    monkeypatch.setattr(app_runner, "service_target", lambda slug, svc: targets[slug])
    return manifests


@pytest.fixture()
def posts(monkeypatch):
    sent: list[dict] = []

    def post(url, *, json, headers, timeout):
        sent.append({"url": url, "json": json, "headers": headers})
        return types.SimpleNamespace(status_code=204)

    monkeypatch.setattr(app_events.httpx, "post", post)
    return sent


def test_only_a_running_subscriber_is_called(apps, posts):
    results = app_events.deliver("copied", {"text": "hi", "source": {"path": "/chat"}})
    assert results == [("on", 204)]
    [sent] = posts
    assert sent["url"] == "http://127.0.0.1:8811/hooks/copied"
    assert sent["headers"] == {"X-Workshop-Event": "copied"}
    body = sent["json"]
    assert body["event"] == "copied" and body["at"]
    assert body["text"] == "hi" and body["source"] == {"path": "/chat"}
    assert body["owner"] is None


def test_an_event_names_the_owner_pinned_where_it_was_raised(apps, posts):
    ownership.set_current_owner(5)
    try:
        app_events.deliver("copied", {"text": "hi", "owner": 9})
        app_events.publish("copied", app_events.Copied(text="t")).join(5)
    finally:
        ownership.clear_current_owner()
    assert [p["json"]["owner"] for p in posts] == [5, 5]


def test_an_unreadable_app_skips_only_itself(apps, monkeypatch, posts):
    def read(d):
        if d == "off":
            raise app_runner.AppError("malformed")
        return apps[d]
    monkeypatch.setattr(app_runner, "_read_manifest_file", read)
    assert app_events.deliver("copied", {"text": "x"}) == [("on", 204)]


def test_a_failing_app_costs_a_result_not_an_exception(apps, monkeypatch):
    def post(*a, **k):
        raise app_events.httpx.ConnectError("refused")
    monkeypatch.setattr(app_events.httpx, "post", post)
    assert app_events.deliver("copied", {"text": "x"}) == [("on", "ConnectError")]


@pytest.fixture()
def client(monkeypatch):
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(app_events, "publish",
                        lambda event, payload: published.append((event, payload.model_dump())))
    app = FastAPI()
    app.include_router(app_events_routes.router)
    return TestClient(app), published


def test_the_route_accepts_a_valid_event_and_publishes_it(client):
    c, published = client
    r = c.post("/app-events/copied", json={"text": "hello", "source": {"path": "/apps"}})
    assert r.status_code == 202 and r.json() == {"event": "copied"}
    assert published == [("copied", {"text": "hello", "source": {"path": "/apps"}})]


def test_the_route_takes_no_owner_from_the_body(client):
    c, published = client
    r = c.post("/app-events/copied", json={"text": "hi", "owner": 9})
    assert r.status_code == 202
    assert "owner" not in published[0][1]


def test_the_route_refuses_an_unknown_event_and_an_invalid_body(client):
    c, published = client
    assert c.post("/app-events/nope", json={"text": "x"}).status_code == 404
    assert c.post("/app-events/copied", json={"text": ""}).status_code == 422
    assert c.post("/app-events/copied", json={}).status_code == 422
    assert published == []


def test_publish_runs_off_the_callers_thread(apps, posts):
    worker = app_events.publish("copied", app_events.Copied(text="t"))
    worker.join(5)
    assert [p["url"] for p in posts] == ["http://127.0.0.1:8811/hooks/copied"]
