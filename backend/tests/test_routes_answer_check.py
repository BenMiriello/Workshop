"""`check_routes_answer` catches the 2026-08-17 outage shape, and says nothing
when the contract is sound.

A gate nobody has watched fail is a gate nobody should trust. The bug it exists
for is a `response_model` field declared as the wrong concrete type: FastAPI
validates the handler's return against it and raises `ResponseValidationError`,
which surfaces as a 500. That defect took the whole chat surface down for seven
minutes and BOTH static gates passed it -- `check_response_model` saw a model
named on the route, `check_opaque_fields` saw a field that was typed.

So this stands up a two-route app in the exact shape that failed:

    GET /things            a collection, so the checker can learn a real id
    GET /things/{id}/sub   declares `branch: str`, hands back a dict

and asserts the checker goes red and names it. The same app with the field
declared correctly must go green, or the check would just be a red light that
is always on.

The parameterised route matters more than the failure itself. The real bug was
on `/chat/threads/{thread_id}/events`, so a checker that probed only the
parameterless routes would have reported clean through the entire outage. The
green case pins the ancestor-id substitution that makes that work: if it
regressed, `/things/{id}/sub` would go unprobed and BOTH cases would pass.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_WORKSHOP = Path(__file__).resolve().parents[2]
_CHECK = _WORKSHOP / "scripts" / "check_routes_answer.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stub_app(branch_type):
    """Two routes, the second with `branch` declared as `branch_type` and always
    handed a dict. `str` is the bug; the dict's own model is the fix."""
    from fastapi import FastAPI
    from pydantic import BaseModel

    class Thing(BaseModel):
        id: str

    class Things(BaseModel):
        items: list[Thing]

    class Sub(BaseModel):
        branch: branch_type | None = None

    app = FastAPI()

    @app.get("/things", response_model=Things)
    async def things():
        return {"items": [{"id": "thing-1"}]}

    @app.get("/things/{thing_id}/sub", response_model=Sub)
    async def sub(thing_id: str):
        # Exactly `chat_branches.branch_state_sync`: an object, never a string.
        return {"branch": {"id": "b1", "name": "Main", "head": "h1"}}

    return app


def _serve(app, port: int):
    import uvicorn

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if getattr(server, "started", False):
            return server
        time.sleep(0.05)
    server.should_exit = True
    pytest.skip("the stub server did not come up")


def _run_check(port: int, schema: dict, tmp_path: Path):
    f = tmp_path / "schema.json"
    f.write_text(json.dumps(schema))
    return subprocess.run(
        [sys.executable, str(_CHECK), "--base", f"http://127.0.0.1:{port}",
         "--schema", str(f), "--list"],
        capture_output=True, text=True, cwd=str(_WORKSHOP), timeout=180,
    )


@pytest.fixture
def stub(tmp_path):
    """Yields a runner bound to a live stub on a free port."""
    made = []

    def run(branch_type):
        port = _free_port()
        app = _stub_app(branch_type)
        server = _serve(app, port)
        made.append(server)
        return _run_check(port, app.openapi(), tmp_path)

    yield run
    for s in made:
        s.should_exit = True


class TestTheCheckCatchesAWrongResponseType:
    def test_a_field_declared_str_and_handed_a_dict_fails_the_check(self, stub):
        r = stub(str)
        assert r.returncode == 1, (
            "the checker passed a route that 500s on ResponseValidationError, "
            f"which is the whole defect it exists for.\n{r.stdout}\n{r.stderr}")
        assert "/things/{thing_id}/sub" in r.stdout, r.stdout
        assert "FAIL 500" in r.stdout, r.stdout

    def test_the_same_route_typed_correctly_passes(self, stub):
        r = stub(dict)
        assert r.returncode == 0, (
            "a sound contract must go green, or the gate is a stuck red light."
            f"\n{r.stdout}\n{r.stderr}")

    def test_the_parameterised_route_is_actually_probed(self, stub):
        """The failure above is only meaningful if the route was REACHED. The
        real bug lived behind a path parameter, so a checker that quietly listed
        it as unprobed would report clean through an outage."""
        r = stub(dict)
        assert "unprobed /things/{thing_id}/sub" not in r.stdout, (
            "the ancestor-id substitution regressed: the parameterised route was "
            f"never called, so neither case proves anything.\n{r.stdout}")


def _scope_stub():
    """A scope-keyed family, the shape `/tasks/{scope}/{task_id}` has.

    Both resolver rules have to fire for this to be reached at all: `{scope}`
    comes from `<head>/scopes` (a list of plain strings, not objects), and
    `{item_id}` comes from the NEAREST ancestor collection, because its own head
    is `/things/alpha`, which is not a route.
    """
    from fastapi import FastAPI
    from pydantic import BaseModel

    class Scopes(BaseModel):
        scopes: list[str]

    class Item(BaseModel):
        id: str

    class Items(BaseModel):
        items: list[Item]

    class Detail(BaseModel):
        ok: bool

    app = FastAPI()

    @app.get("/things/scopes", response_model=Scopes)
    async def scopes():
        return {"scopes": ["alpha"]}

    @app.get("/things", response_model=Items)
    async def things():
        return {"items": [{"id": "item-1"}]}

    @app.get("/things/{scope}/{item_id}", response_model=Detail)
    async def detail(scope: str, item_id: str):
        # 500 (not 404) unless BOTH parameters were filled from the right
        # places. A 404 counts as "answered", so a resolver regression that
        # reached this route with junk would pass a test that only asked whether
        # it was probed. Failing loudly is what makes the assertion below real.
        if scope != "alpha" or item_id != "item-1":
            raise RuntimeError(f"resolver supplied {scope!r}/{item_id!r}")
        return {"ok": True}

    return app


class TestScopeKeyedRoutesAreReached:
    def test_a_scope_and_an_id_are_both_filled_from_the_right_collection(self, tmp_path):
        port = _free_port()
        app = _scope_stub()
        server = _serve(app, port)
        try:
            r = _run_check(port, app.openapi(), tmp_path)
            assert "unprobed /things/{scope}/{item_id}" not in r.stdout, (
                "the scope-keyed route was never reached, so the ticket-detail "
                f"family is uncovered.\n{r.stdout}")
            # The stub 500s on wrong ids, so green here means the scope came
            # from /things/scopes and the id from /things, not merely that
            # something was substituted.
            assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        finally:
            server.should_exit = True
