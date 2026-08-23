"""`routes/control.py` -- queue, steer and preempt over HTTP.

Steps B6 and B7 of
`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`.

Worth a live request rather than a direct service call for the two reasons
`test_branches_routes.py` gives: FastAPI silently drops any key its
`response_model` does not name (so the honest steer copy, the snapshot sha and
the two heads have to be checked over the wire), and an unwired router is not
done.

The one rule this file exists to pin hardest: **a preempt with no human action
is a 403**, and the route has no way to supply one on the caller's behalf.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import control as control_routes  # noqa: E402
from services import control as control_svc  # noqa: E402
from services import run_control  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("WORKSHOP_CONTROL_DIR", str(tmp_path / "control"))
    app = FastAPI()
    app.include_router(control_routes.router)
    return TestClient(app)


class TestTheRouterIsAResource:
    def test_it_declares_a_prefix_and_a_tag(self):
        assert control_routes.router.prefix == "/control"
        assert control_routes.router.tags == ["control"]

    def test_every_route_declares_a_response_model(self):
        for route in control_routes.router.routes:
            assert getattr(route, "response_model", None) is not None, (
                f"{route.path} has no response_model, so the generated client "
                f"types it as unknown")

    def test_it_is_mounted_in_the_app(self):
        from main import app

        paths = {r.path for r in app.routes}
        assert "/control/{session_id}" in paths
        assert "/control/{session_id}/requests" in paths
        assert "/control/{session_id}/requests/{request_id}" in paths
        assert "/control/{session_id}/preempt" in paths


class TestQueueAndSteer:
    def test_a_queued_request_is_fifo_and_cancellable_over_the_wire(self, client):
        for text in ("first", "second"):
            r = client.post("/control/sweep-1/requests",
                            json={"text": text, "verb": "queue"})
            assert r.status_code == 200, r.text
        channel = client.get("/control/sweep-1").json()
        assert [q["text"] for q in channel["requests"]] == ["first", "second"]
        rid = channel["requests"][0]["id"]
        gone = client.delete(f"/control/sweep-1/requests/{rid}").json()
        assert gone["cancelled"] is True
        assert [q["text"] for q in client.get("/control/sweep-1").json()
                ["requests"]] == ["second"]

    def test_cancelling_what_is_not_there_is_false_and_not_a_404(self, client):
        r = client.delete("/control/sweep-1/requests/nope")
        assert r.status_code == 200 and r.json()["cancelled"] is False

    def test_the_wire_shape_carries_no_priority(self, client):
        client.post("/control/sweep-1/requests",
                    json={"text": "a thing", "verb": "steer"})
        request = client.get("/control/sweep-1").json()["requests"][0]
        assert set(request) == {"id", "verb", "text", "actor", "at"}

    def test_a_steer_is_served_with_copy_that_does_not_promise_now(self, client):
        out = client.post("/control/sweep-1/requests",
                          json={"text": "also update the docs", "verb": "steer",
                                "actor": "operator"}).json()
        assert out["delivered"] is False
        assert out["detail"] == control_svc.STEER_COPY_RUN
        # Section 8: "The UI must not label this `now`." Checked here as well as
        # in the service because `response_model` is what a client sees.
        assert "now" not in out["detail"].lower()

    def test_preempt_is_not_reachable_as_a_verb_on_the_requests_route(self,
                                                                     client):
        r = client.post("/control/sweep-1/requests",
                        json={"text": "stop", "verb": "preempt"})
        assert r.status_code == 400
        assert "preempt" in r.json()["detail"].lower()

    def test_an_empty_request_is_a_422_from_the_model(self, client):
        assert client.post("/control/sweep-1/requests",
                           json={"text": ""}).status_code == 422

    def test_a_session_with_nothing_waiting_is_an_empty_200(self, client):
        body = client.get("/control/never-heard-of-it").json()
        assert body["requests"] == [] and body["yield_requested_at"] is None

    def test_the_collection_lists_channels_that_exist(self, client):
        client.post("/control/sweep-1/requests", json={"text": "a"})
        client.post("/control/sweep-2/requests", json={"text": "b"})
        body = client.get("/control").json()
        assert body["count"] == 2
        assert {c["session_id"] for c in body["channels"]} == {"sweep-1",
                                                              "sweep-2"}


class TestPreemptNeedsAHumanAction:
    """Section 8's authority rule, on the wire.

    Authority is bound to a live human action, NOT to a session kind, because
    every chat acts on the operator's behalf. An agent may never preempt another
    agent; it queues or asks.
    """

    def _body(self, **over):
        body = {"subject_id": "wt-1", "new_holder_id": "chat-9",
                "actor": "agent:maker", "human_action": "confirmed by hand",
                "grace_sec": 0}
        body.update(over)
        return body

    def test_a_missing_human_action_is_refused_by_the_model(self, client):
        body = self._body()
        body.pop("human_action")
        assert client.post("/control/sweep-1/preempt",
                           json=body).status_code == 422

    def test_an_empty_human_action_is_refused_by_the_model(self, client):
        assert client.post("/control/sweep-1/preempt",
                           json=self._body(human_action="")).status_code == 422

    def test_a_blank_human_action_is_a_403_from_the_verb(self, client,
                                                         monkeypatch):
        """Whitespace satisfies `min_length` and must not satisfy the rule."""
        r = client.post("/control/sweep-1/preempt",
                        json=self._body(human_action="   "))
        assert r.status_code == 403
        assert "queues" in r.json()["detail"]

    def test_there_is_no_default_for_it_in_the_request_model(self):
        field = control_routes.PreemptIn.model_fields["human_action"]
        assert field.is_required(), (
            "a default here would let an agent preempt by omission, which is "
            "the exact rule section 8 states")


class TestPreemptOverTheWire:
    def test_the_response_carries_the_snapshot_and_both_heads(self, client,
                                                              monkeypatch,
                                                              tmp_path):
        """`response_model` must not drop the fields that prove section 9."""
        async def fake_preempt(**kw):
            return {"subject_kind": "worktree", "subject_id": "wt-1",
                    "preempted": True, "forced": True, "yielded": False,
                    "holder_id": "sweep-42", "holder_kind": "run",
                    "grace_sec": 0.0, "stop_detail": "SIGTERM to pid 1",
                    "quiesce": {"repo": "/tmp/tree", "ok": True,
                                "cleared_index_lock": True,
                                "snapshot_sha": "abc123",
                                "snapshot_ref": "refs/wip/sweep-42",
                                "tree_was_clean": False, "restored": True,
                                "removed": ["new_file.txt"], "clean": True,
                                "head_before": "deadbee", "head_after": "deadbee",
                                "detail": ""},
                    "lease": {"subject_kind": "worktree", "subject_id": "wt-1",
                              "holder_kind": "chat", "holder_id": "chat-9",
                              "generation": 4, "state": "held"},
                    "detail": control_svc.PREEMPT_COPY}

        monkeypatch.setattr(control_svc, "preempt", fake_preempt)
        body = client.post("/control/sweep-42/preempt",
                           json={"subject_id": "wt-1",
                                 "new_holder_id": "chat-9",
                                 "actor": "benmiriello",
                                 "human_action": "take over",
                                 "grace_sec": 0}).json()
        q = body["quiesce"]
        assert q["snapshot_sha"] == "abc123"
        assert q["snapshot_ref"] == "refs/wip/sweep-42"
        assert q["head_before"] == q["head_after"] == "deadbee"
        assert q["cleared_index_lock"] is True and q["clean"] is True
        assert body["lease"]["holder_id"] == "chat-9"
        assert body["lease"]["generation"] == 4

    def test_no_lease_store_is_a_503_and_not_a_refusal(self, client,
                                                       monkeypatch):
        from services import leases as leases_svc

        async def down(**kw):
            raise leases_svc.LeasesUnavailable("no relational store")

        monkeypatch.setattr(control_svc, "preempt", down)
        r = client.post("/control/sweep-42/preempt",
                        json={"subject_id": "wt-1", "new_holder_id": "chat-9",
                              "actor": "benmiriello",
                              "human_action": "take over", "grace_sec": 0})
        assert r.status_code == 503

    def test_asking_to_yield_shows_up_on_the_channel(self, client):
        run_control.request_yield("sweep-42", actor="benmiriello",
                                 reason="I need this branch")
        body = client.get("/control/sweep-42").json()
        assert body["yield_requested_by"] == "benmiriello"
        assert body["yield_reason"] == "I need this branch"
        assert body["yield_requested_at"]
