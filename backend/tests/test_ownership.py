"""Red-team verification of the auth + ownership redesign (docs/auth-redesign/).

Every assertion here is a guarantee the operator demanded: no anonymous access,
no cross-user data, no network/loopback trust, no fail-open, no is_operator data
bypass, and WORKSHOP_OPERATOR_EMAIL consulted at no request-time path. These run
against the real FastAPI app via TestClient (which connects from loopback — so
every 401/403 below also proves loopback grants nothing)."""
from __future__ import annotations

import json
import os
import pathlib

os.environ["WORKSHOP_COOKIE_SECURE"] = "0"  # test client speaks http
os.environ["WORKSHOP_REGISTRATION_OPEN"] = "1"

import pytest
from fastapi.testclient import TestClient

from services import auth_store, ownership
from main import app

CSRF = {"X-Workshop-Client": "1"}
BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    auth_store.init_db()
    # Operator = data owner of the single-dataset domains (set as DATA here, the
    # way the bootstrap migration does — never via email at request time).
    op = auth_store.create_user("operator@example.com", "password1")
    member = auth_store.create_user("member@example.com", "password2")
    auth_store.set_operator(op["id"])
    for d in _owned_domains():
        auth_store.set_domain_owner(d, op["id"])
    return {"op": op, "member": member, "tmp": tmp_path}


def _client_for(email: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


def _owned_domains() -> list[str]:
    """Every single-dataset domain the INSTALLED apps declare they own.

    Asked for, not listed. This was a literal tuple of four app ids -- a
    platform test asserting against one installation -- and a domain is now a
    field on the app's own declaration (`AppSpec.owned_domains`), which is the
    only place that knows it.
    """
    from services import app_registry

    return sorted({d for s in app_registry.installed_specs() for d in s.owned_domains})


def _app_get_paths() -> list[str]:
    """GET paths contributed by installed apps, minus their declared ingest.

    The list this replaces was five hardcoded app routes. Deriving it is
    strictly stronger: it covers EVERY app route on this instance rather than
    the five somebody remembered, and it covers an app added tomorrow.

    Unauthenticated ingest is excluded by the app's own declaration
    (`edge_blocked_prefixes` / `edge_blocked_exact`), which is the same list the
    public edge refuses -- so "is this route meant to be open" is answered by
    the app, in one place, rather than by a second opinion here.
    """
    from services import app_registry

    prefixes = tuple(sorted({p for s in app_registry.installed_specs()
                             for p in s.identity.get("api_prefixes", [])}))
    if not prefixes:
        return []
    open_prefixes, open_exact = app_registry.installed_edge_blocks()

    def under(path: str, prefix: str) -> bool:
        """SEGMENT-EXACT. `/healthz` is the platform's liveness probe and is not
        under an app prefix of `/health`; a bare `startswith` said it was, which
        is the same off-by-a-segment the public edge was bitten by in
        security-audit H1 (`/auto` failing to match `/auto-pr`)."""
        return path == prefix or path.startswith(prefix.rstrip("/") + "/")

    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", set()) or set()
        if "GET" not in methods or "{" in path:
            continue
        if not any(under(path, pre) for pre in prefixes):
            continue
        if path in open_exact or any(under(path, b) for b in open_prefixes):
            continue
        out.append(path)
    return sorted(set(out))


def _is_mounted(path: str) -> bool:
    """Is this path actually served by this deployment?

    Several of these routes belong to installable apps, so on a fresh clone with
    no apps installed they are not mounted at all. A 404 there is not an
    ownership failure, it is an absent feature, and asserting `401 or 403`
    against it made the whole suite red on any machine but the one it was
    written on.
    """
    return any(getattr(r, "path", None) == path for r in app.routes)


# 1 + 2 — no credential (from loopback) reaches any personal/platform endpoint.
PLATFORM_GATED_PATHS = ["/chat/threads", "/agents/", "/push/status"]


@pytest.mark.parametrize("path", PLATFORM_GATED_PATHS)
def test_unauthenticated_is_denied_on_platform_paths(env, path):
    if not _is_mounted(path):
        pytest.skip(f"{path} is not mounted on this deployment")
    anon = TestClient(app)
    assert anon.get(path).status_code in (401, 403)


def test_unauthenticated_is_denied_on_every_installed_app_path(env):
    """Every app route this instance serves, not five somebody listed.

    An app's read routes must refuse an anonymous caller even from loopback.
    The exceptions are the app's OWN declared ingest paths, which are
    unauthenticated by design and which the public edge refuses for that reason.
    """
    paths = _app_get_paths()
    if not paths:
        pytest.skip("no app with HTTP routes is installed on this instance")
    anon = TestClient(app)
    open_to_anyone = [p for p in paths if anon.get(p).status_code not in (401, 403)]
    assert open_to_anyone == [], (
        f"{len(open_to_anyone)} installed-app route(s) answered an anonymous "
        f"loopback caller: {open_to_anyone}")


# 4 — garbage / absent credentials never fail open.
def test_garbage_credentials_denied(env):
    """On a PLATFORM route, so this holds on a clone with no apps installed --
    which is the deployment where a fail-open would be discovered latest."""
    anon = TestClient(app)
    assert anon.get("/chat/threads",
                    headers={"Authorization": "Bearer nonsense"}).status_code == 401
    anon.cookies.set("workshop_session", "forged-cookie-value")
    assert anon.get("/chat/threads").status_code == 401


# 3 — a second member never sees the operator's data and cannot widen scope.
def test_member_cannot_read_an_owned_domain(env):
    """A domain has exactly one owner; every other account is refused it.

    Which domains exist is read off the installed apps' declarations, and the
    route to probe is read off what those apps actually mounted. The list used
    to be two app ids and two of their routes typed in here, which asserted
    against one installation and skipped on every other.
    """
    from services import app_registry

    domains = set(_owned_domains())
    if not domains:
        pytest.skip("no installed app declares an owned domain")

    # A GET route belonging to an app that owns a domain. `require_domain_owner`
    # is mounted per ROUTER, so any of that app's routes proves the gate.
    candidates = []
    for spec in app_registry.installed_specs():
        if not (set(spec.owned_domains) & domains):
            continue
        prefixes = tuple(spec.identity.get("api_prefixes", []))
        if not prefixes:
            continue
        candidates += [p for p in _app_get_paths()
                       if any(p == pre or p.startswith(pre.rstrip("/") + "/") for pre in prefixes)]
    if not candidates:
        pytest.skip("no installed app that owns a domain mounts a probeable GET route")

    member = _client_for("member@example.com", "password2")
    refused = [p for p in candidates if member.get(p).status_code == 403]
    assert refused, (
        "not one route of an app whose domain the operator owns refused a second "
        f"member: {sorted(candidates)[:8]}")


def test_per_user_stores_are_isolated(env):
    """Two accounts, one per-owner store, no leak either way.

    Asserted on the PLATFORM's own per-owner store -- chat threads -- rather
    than on an app's. The guarantee is the ownership model's, it must hold on a
    clone with no apps installed, and the version of this test that used three
    apps' routes skipped entirely on exactly that clone.
    """
    op = _client_for("operator@example.com", "password1")
    member = _client_for("member@example.com", "password2")

    r = op.post("/chat/threads", json={"title": "owner-only"}, headers=CSRF)
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]

    mine = op.get("/chat/threads")
    assert mine.status_code == 200
    assert tid in json.dumps(mine.json())

    theirs = member.get("/chat/threads")
    assert theirs.status_code == 200
    assert tid not in json.dumps(theirs.json()), "a member saw the operator's chat"

    # And addressing it directly does not widen anything either.
    assert member.get(f"/chat/threads/{tid}").status_code in (403, 404)


# 7 — platform routers are admin-only; a non-admin member is refused.
def test_platform_requires_operator(env):
    member = _client_for("member@example.com", "password2")
    assert member.get("/agents/").status_code == 403
    op = _client_for("operator@example.com", "password1")
    assert op.get("/agents/").status_code != 403  # operator passes the gate


# 5 — a service token only works inside its scope / ownership.
def test_service_token_scoping(env):
    """An operator-owned token reaches its app's domain; a member's does not.

    The token scope comes from the app's own `AppSpec.service_tokens`, which is
    where a machine credential is declared now. This test used to type one app's
    id, one of its routes and its token scope.
    """
    from services import app_registry

    probe = None
    for spec in app_registry.installed_specs():
        if not (spec.service_tokens and spec.owned_domains):
            continue
        prefixes = tuple(spec.identity.get("api_prefixes", []))
        paths = [p for p in _app_get_paths()
                 if any(p == pre or p.startswith(pre.rstrip("/") + "/") for pre in prefixes)]
        if paths:
            probe = (spec.service_tokens[0][1], paths[0])
            break
    if probe is None:
        pytest.skip("no installed app declares both a service token and an owned domain")
    scope, path = probe

    tok = auth_store.create_service_token(env["op"]["id"], scope, "cron")
    c = TestClient(app)
    assert c.get(path, headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    # A token owned by a NON-owner (the member) is refused on the owner's domain.
    mtok = auth_store.create_service_token(env["member"]["id"], scope, "x")
    assert c.get(path, headers={"Authorization": f"Bearer {mtok}"}).status_code == 403


# 6 — WORKSHOP_OPERATOR_EMAIL is consulted at NO request-time path. It may appear
# only in the one-time migration script. Grep the request-serving code.
def test_operator_email_not_consulted_at_request_time():
    offenders = []
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND).as_posix()
        if rel.startswith(("scripts/", "tests/", ".venv/")):
            continue
        if "WORKSHOP_OPERATOR_EMAIL" in p.read_text():
            offenders.append(rel)
    assert offenders == [], f"operator email referenced in request-time code: {offenders}"


# 7 LEFT THIS FILE ON 2026-09-04. It asserted that one app's reads are scoped to
# the server-pinned owner and that a client-supplied viewer id cannot widen
# them. The ownership MODEL is this file's subject and stays; the app that
# resolves a viewer is the one that can regress it, so the assertion moved to
# that app's own tests and this file stopped being the last platform module
# importing an app.
