"""A user's saved app order: kept on the server, and arranging the tiles one
view shows leaves the tiles it does not show where they were."""
import pytest
from fastapi.testclient import TestClient

from main import app
from services import auth_store, dashboard_order

CSRF = {"X-Workshop-Client": "1"}


@pytest.fixture
def user(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setenv("WORKSHOP_COOKIE_SECURE", "0")
    auth_store.init_db()
    return auth_store.create_user("op@example.com", "password1")


def _signed_in() -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": "op@example.com", "password": "password1"}).status_code == 200
    return c


def test_the_order_starts_empty_and_is_kept(user):
    c = _signed_in()
    assert c.get("/dashboard/layout").json() == {"order": []}
    assert c.put("/dashboard/layout", json={"order": ["b", "a"]}, headers=CSRF).json() == {"order": ["b", "a"]}
    assert _signed_in().get("/dashboard/layout").json() == {"order": ["b", "a"]}


def test_tiles_a_view_does_not_show_keep_their_slots(user):
    dashboard_order.set_order(user["id"], ["a", "hidden", "b", "c"])
    assert dashboard_order.set_order(user["id"], ["c", "b", "a"]) == ["c", "hidden", "b", "a"]


def test_new_tiles_go_after_the_saved_ones(user):
    dashboard_order.set_order(user["id"], ["a", "b"])
    assert dashboard_order.set_order(user["id"], ["b", "a", "new"]) == ["b", "a", "new"]
