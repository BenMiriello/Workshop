"""What happens to a Workshop copy, kept per user: the store and its routes."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import copy_prefs as routes
from services import copy_prefs


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = tmp_path / "copies" / "prefs.json"
    monkeypatch.setattr(copy_prefs, "store_path", lambda: path)
    return path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_copies_are_saved_and_reach_the_system_until_changed():
    assert copy_prefs.current() == {"save": True, "destination": "system"}


def test_a_change_keeps_what_it_leaves_out(store):
    copy_prefs.update(destination="workshop")
    assert copy_prefs.current() == {"save": True, "destination": "workshop"}
    copy_prefs.update(save=False)
    assert copy_prefs.current() == {"save": False, "destination": "workshop"}
    assert store.exists()


def test_an_unreadable_store_reads_as_the_defaults(store):
    store.parent.mkdir(parents=True)
    store.write_text('{"save": "yes", "destination": "elsewhere"}')
    assert copy_prefs.current() == {"save": True, "destination": "system"}


def test_routes_read_and_change_them(client):
    assert client.get("/copies/prefs").json() == {"save": True, "destination": "system"}
    r = client.put("/copies/prefs", json={"destination": "workshop"})
    assert r.json() == {"save": True, "destination": "workshop"}
    assert client.put("/copies/prefs", json={"destination": "nowhere"}).status_code == 422
