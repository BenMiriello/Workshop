"""`/leases`: what is claimed, and whether anyone is still there.

WHY THIS ROUTE HAD TO EXIST rather than being a query parameter on
`/presence/directory`, which is the question a reviewer should ask first. The
directory joins OUTWARD FROM LIVE PRESENCE, so the two rows a stuck-work view
most needs are the two it structurally cannot produce: an `expired` lease, and a
held one whose holder stopped writing presence. Both have no session to join
from, so the directory renders abandoned work as silence.
`test_the_directory_cannot_answer_this` is that claim, exercised rather than
asserted in a comment.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import leases as leases_routes  # noqa: E402
from services import leases as leases_svc  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(leases_routes.router)
    return TestClient(app)


class TestTheRouterIsAResource:
    def test_it_declares_a_prefix_and_a_tag(self):
        assert leases_routes.router.prefix == "/leases"
        assert leases_routes.router.tags == ["leases"]

    def test_every_route_declares_a_response_model(self):
        for route in leases_routes.router.routes:
            assert getattr(route, "response_model", None) is not None, (
                f"{route.path} has no response_model, so the generated client "
                f"types it as unknown")

    def test_it_is_mounted_in_the_app(self):
        from main import app

        assert "/leases" in {r.path for r in app.routes}

    def test_it_is_read_only(self):
        """Minting a lease for a holder that is not running would create the
        orphaned tree this whole design removes; taking one away is preempt,
        which carries the human-action rule."""
        methods: set[str] = set()
        for route in leases_routes.router.routes:
            methods |= set(getattr(route, "methods", set()))
        assert methods == {"GET"}, methods


class TestTheShape:
    def test_an_installation_with_no_store_answers_200_and_says_so(
            self, client, monkeypatch):
        """`available: false` and `leases: []` are DIFFERENT ANSWERS from
        `available: true` and `leases: []`, and they look identical in the list
        alone. A 503 would be wrong too: an installation that keeps no leases is
        a fact to render calmly, not an error to retry."""
        def boom(**_):
            raise leases_svc.LeasesUnavailable("no store")
        monkeypatch.setattr(leases_svc, "list_leases", boom)
        body = client.get("/leases").json()
        assert body["available"] is False
        assert body["leases"] == [] and body["count"] == 0
        # the windows still come back, because a client renders copy from them
        assert body["windows"]["idle_after_sec"] == leases_svc.IDLE_AFTER_SEC

    def test_the_windows_are_served_so_no_client_hardcodes_them(self, client,
                                                                monkeypatch):
        from services import control as control_svc

        monkeypatch.setattr(leases_svc, "list_leases", lambda **_: [])
        w = client.get("/leases").json()["windows"]
        assert w == {
            "lease_ttl_sec": leases_svc.LEASE_TTL_SEC,
            "heartbeat_sec": leases_svc.HEARTBEAT_SEC,
            "idle_after_sec": leases_svc.IDLE_AFTER_SEC,
            "preempt_grace_sec": int(control_svc.PREEMPT_GRACE_SEC),
        }

    def test_elapsed_seconds_are_derived_on_the_server(self, client, monkeypatch):
        """A browser subtracting its own clock from these timestamps would fold
        in skew, and "last beat 40 seconds ago" is a number the operator acts
        on."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        monkeypatch.setattr(leases_svc, "list_leases", lambda **_: [{
            "id": "row-1",
            "subject_kind": "worktree", "subject_id": "tree-1",
            "holder_kind": "run", "holder_id": "run-a",
            "principal_user_id": None, "generation": 3, "state": "held",
            "idle_since": None,
            "acquired_at": (now - timedelta(seconds=300)).isoformat(),
            "expires_at": (now + timedelta(seconds=60)).isoformat(),
            "last_renewed_at": (now - timedelta(seconds=40)).isoformat(),
            "last_work_at": (now - timedelta(seconds=90)).isoformat(),
        }])
        row = client.get("/leases").json()["leases"][0]
        assert 299 <= row["held_for_sec"] <= 302
        assert 39 <= row["since_heartbeat_sec"] <= 42
        assert 89 <= row["since_work_sec"] <= 92
        # `id` is dropped: the subject is the identity a caller addresses
        assert "id" not in row

    def test_held_only_defaults_to_false_because_the_dead_ones_are_the_point(
            self, client, monkeypatch):
        seen: dict = {}

        def spy(**kw):
            seen.update(kw)
            return []
        monkeypatch.setattr(leases_svc, "list_leases", spy)
        client.get("/leases")
        assert seen["held_only"] is False, (
            "an expired lease over a tree that still exists on disk is "
            "abandoned work, and nothing else in the system reports it")

    def test_an_unknown_kind_is_a_400_naming_the_closed_set(self, client):
        r = client.get("/leases", params={"subject_kind": "nonsense"})
        assert r.status_code == 400
        assert "worktree" in r.json()["detail"]
        r = client.get("/leases", params={"holder_kind": "nonsense"})
        assert r.status_code == 400
        assert "chat" in r.json()["detail"]


class TestTheRowShapeDoesNotDriftFromTheDirectory:
    def test_a_lease_row_extends_the_directory_s_lease_ref(self):
        """Two hand-kept copies of these fields is the drift ADR 0023 clause 6
        exists to stop, so the list row IS the directory's `LeaseRef` plus three
        derived numbers."""
        from routes.presence import LeaseRef

        assert issubclass(leases_routes.LeaseRow, LeaseRef)
        extra = set(leases_routes.LeaseRow.model_fields) - set(LeaseRef.model_fields)
        assert extra == {"held_for_sec", "since_heartbeat_sec", "since_work_sec"}


def test_the_directory_cannot_answer_this(tmp_path, monkeypatch):
    """The reason the route exists, exercised rather than asserted in a comment.

    `presence.directory` builds its rows by walking the PRESENCE CHANNEL. Point
    it at an empty channel -- which is what a holder that stopped beating leaves
    behind -- and hand it a `lease_source` that insists a lease is held. The
    directory still answers with nothing, because there is no session to join
    from. That row is an abandoned tree, and it is invisible there by
    construction, which is why `/leases` starts from the lease table instead.
    """
    from services import presence

    channel = tmp_path / "presence"
    channel.mkdir()
    monkeypatch.setattr(presence, "PRESENCE_DIR", channel)

    calls: list[tuple[str, str]] = []

    def insistent_lease(kind: str, sid: str) -> dict:
        calls.append((kind, sid))
        return {"subject_kind": kind, "subject_id": sid, "holder_kind": "run",
                "holder_id": "a-holder-nobody-can-see", "generation": 1,
                "state": "held"}

    entries = presence.directory(lease_source=insistent_lease)
    assert entries == [], (
        "the directory is a join starting from presence: with no live session "
        "there is no row to decorate, whatever the lease table says")
    assert calls == [], (
        "it never even asked about a lease, because it iterates sessions and "
        "there were none -- the abandoned holder is not a row it can miss, it "
        "is a row it cannot construct")
