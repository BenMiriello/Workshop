#!/usr/bin/env python3
"""Every GET route actually answers. The third half of ADR 0023 clause 6.

THE FINDING THIS PROTECTS. On 2026-08-17 the chat surface went down for seven
minutes: `ThreadEvents.branch` was declared `str | None` while the handler
returns `branch_state_sync`'s `{id, name, head}` dict, so FastAPI raised
`ResponseValidationError` and `GET /chat/threads/{id}/events` -- the endpoint
the transcript, the live poll, the compact boundary and the send queue all ride
on -- returned 500 for every thread that had a relational branch row.

Both existing gates passed it, and that is the point of this one:

    check_response_model.py   is a model NAMED on the route?      yes -> pass
    check_opaque_fields.py    is the field typed as NOTHING?      no  -> pass
    (nothing)                 does the declared type MATCH what
                              the handler returns?                     <- here

Adding a `response_model` is not a documentation change, it is a runtime
behaviour change: FastAPI validates every response against it and 500s on a
mismatch. The two static gates can see the declaration and cannot see the
value, and no static checker can -- a payload is assembled from service calls
across modules, so "what does this handler return" is not decidable by reading
the route. The only sound oracle is FastAPI's own validation, which means the
routes have to actually be CALLED.

WHY THIS IS NOT A UNIT TEST WITH FIXTURES. The bug only appeared for threads
that HAVE a branch row; a thread without one returns `branch=None`, which
validates. A synthetic fixture would have been built from the same wrong
understanding as the model and would have passed. So this probes the real
deployment against real data, and that is deliberate. It is also why it is
read-only: GET only, never a mutating verb.

PATH PARAMETERS ARE THE WHOLE DIFFICULTY, and getting them wrong is how this
check would have missed the bug it exists for. 62 of 195 GET routes take a path
parameter and `/chat/threads/{thread_id}/events` is one of them -- a sweep of
only the parameterless routes finds nothing. Rather than hardcode a table of
ids, which goes stale silently, the parent collection supplies them: for a
route `/a/b/{param}/...` the checker looks up `/a/b`, which it has already
probed, and takes `id` or `slug` off the objects in that response. That is
derived from the tree rather than asserted about it, so a new resource family
is covered the day its list route exists.

WHAT COUNTS AS FAILURE. Only 5xx. A 401, 403, 404 or 422 means the route was
reached and answered, which is all this check is about; asserting anything
finer would be re-testing the routes' own behaviour and would make this fire
constantly for unrelated reasons, which is how a gate gets switched off.

NOT PROBED IS REPORTED, NEVER SILENT. A route the checker could not reach (no
id available, deliberately skipped, timed out) is counted and named under
`--list`. A checker that quietly covers half the surface while printing "clean"
is worse than no checker, because it is trusted.

SKIPPED DELIBERATELY. Streaming GETs (`.../stream`) never complete, and the
app-runner proxies reach out to other processes; both would hang or fail for
reasons that have nothing to do with the response contract.

Exit codes: 0 every probed route answered, 1 something returned 5xx, 2 the
schema could not be generated, 3 there was no backend to probe.

Usage:
    python3 scripts/check_routes_answer.py            # probe, exit 1 on any 5xx
    python3 scripts/check_routes_answer.py --json
    python3 scripts/check_routes_answer.py --list     # per-route detail
    python3 scripts/check_routes_answer.py --base http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_BACKEND = _WORKSHOP / "backend"
_DEFAULT_BASE = "http://127.0.0.1:8000"

# Reached-and-answered. Anything else in 5xx is the failure this looks for.
_OK_CEILING = 500

# Routes that cannot be probed by asking for them, with the reason. Matched as
# a substring of the OpenAPI path.
_SKIP = {
    "/stream": "a streaming response never completes",
    "/proxy/": "reaches into an app-runner container, not this contract",
    "/base-apps/run/": "reaches into an app-runner container, not this contract",
}

_PARAM_RE = re.compile(r"\{([^}]+)\}")


def _schema() -> dict:
    """Regenerate rather than read a committed artifact, for the reason
    `check_generated_client` gives: a cached file passes for whoever last
    touched it, and the point is to say something true about THIS tree."""
    out = subprocess.run(
        [sys.executable, str(_WORKSHOP / "scripts" / "gen_openapi.py"),
         "--schema-only", "--schema-out", "/dev/stdout", "--quiet"],
        capture_output=True, text=True, cwd=str(_WORKSHOP),
    )
    if out.returncode != 0:
        print(f"check_routes_answer: could not generate the schema:\n{out.stderr[-2000:]}",
              file=sys.stderr)
        raise SystemExit(2)
    return json.loads(out.stdout)


def _mint_session() -> tuple[str, object]:
    """A real session for the data owner, revoked in `finally` by the caller.

    The probe has to be authenticated or every route answers 401 and the check
    proves nothing. Owner 6 is the app user that holds the real data (the
    `app-user-id-vs-linux-uid` note); a synthetic user would have no threads and
    the parameterised routes would all go unprobed.
    """
    sys.path.insert(0, str(_BACKEND))
    from services import auth_store  # noqa: E402

    return auth_store.create_session(
        6, user_agent="check_routes_answer (revoked at exit)", ttl_days=1), auth_store


def _get(base: str, path: str, token: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(base + path, method="GET")
    req.add_header("Cookie", f"workshop_session={token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, b""


def _ids_from(body: bytes) -> list[str]:
    """`id` / `slug` values off the objects in a collection response.

    Handles both shapes this app serves: a bare list, and an envelope whose
    first list-valued key holds the items (`{"items": [...]}`,
    `{"threads": [...]}`).
    """
    try:
        doc = json.loads(body)
    except Exception:
        return []
    items = None
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        for v in doc.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                items = v
                break
    if items is None:
        # A list of plain STRINGS is the other collection shape this app serves,
        # and it is how scopes arrive: `/tasks/scopes` -> {"scopes": [...]}.
        if isinstance(doc, dict):
            for v in doc.values():
                if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                    return [x for x in v if x]
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        for key in ("id", "slug"):
            v = it.get(key)
            if isinstance(v, str) and v:
                out.append(v)
                break
    return out


def _resolve(name: str, head: str, pool: dict[str, list[str]]) -> str | None:
    """A real value for `{name}` appearing after `head`.

    Two rules, both derived from how this app names things rather than from a
    table of ids that would go stale in silence:

      1. A parameter whose name mentions a scope comes from `<head>/scopes`.
         `/tasks/scopes`, `/notes/scopes` and `/memory-policy/scopes` are all
         spelled that way, so a scope-keyed route is reachable without knowing
         which scopes this instance happens to have.
      2. Otherwise the NEAREST already-probed ancestor collection supplies it,
         walking up a segment at a time. Nearest rather than exact so the second
         parameter of `/tasks/{scope}/{task_id}` still resolves: its head is
         `/tasks/development`, which is nothing, and `/tasks` is one segment up.

    THE LIMIT, stated rather than hidden: an id that does not belong to the
    route it lands in gives a 404, and a 404 counts as answered here. That is
    still a reached route -- routing, dependencies and auth all ran -- but it
    stops short of serialising a real object, which is the part that catches a
    wrong `response_model`. Rule 2 walking UP is what keeps that rare: the
    nearest collection is almost always the right family.
    """
    if "scope" in name and f"{head}/scopes" in pool:
        return pool[f"{head}/scopes"][0]
    probe = head
    while probe:
        if probe in pool:
            return pool[probe][0]
        if "/" not in probe.strip("/"):
            break
        probe = probe.rsplit("/", 1)[0]
    return None


def _skip_reason(path: str) -> str | None:
    for frag, why in _SKIP.items():
        if frag in path:
            return why
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=_DEFAULT_BASE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--timeout", type=float, default=15.0)
    # Testability, and only that: the checker's own logic (ancestor id
    # substitution, what counts as failure) is pinned by pointing it at a stub
    # app whose schema is not this tree's. Nothing in normal use passes it.
    ap.add_argument("--schema", help="read the OpenAPI schema from a file "
                                     "instead of regenerating it")
    args = ap.parse_args()

    # COULD NOT RUN GETS ITS OWN EXIT CODE, and is not folded into either
    # answer. "The backend is down" is not "the contract is broken", so it must
    # not be a failure; but it is not a pass either, and returning 0 would let
    # the reviewer gate print clean for a check that never executed. 3 lets the
    # caller record it as ambiguous, which is what reviewer.sh does with it and
    # what LATITUDE=strict is there to escalate.
    if _get(args.base, "/healthz", "", 5.0)[0] == 0:
        print(f"check_routes_answer: no backend at {args.base}, so nothing was "
              "probed (start it, or pass --base)")
        return 3

    schema = json.loads(Path(args.schema).read_text()) if args.schema else _schema()
    gets = sorted(p for p, ops in schema.get("paths", {}).items() if "get" in ops)

    token, auth_store = _mint_session()
    failed: list[dict] = []
    ok: list[str] = []
    skipped: list[dict] = []
    unprobed: list[dict] = []
    id_pool: dict[str, list[str]] = {}

    try:
        # Phase 1: the parameterless routes. These are also what supplies the
        # ids Phase 2 needs, which is why they go first.
        for path in gets:
            if "{" in path:
                continue  # phase 2, including its skips, so nothing is counted twice
            why = _skip_reason(path)
            if why:
                skipped.append({"path": path, "why": why})
                continue
            code, body = _get(args.base, path, token, args.timeout)
            if code == 0:
                unprobed.append({"path": path, "why": "timed out or refused"})
                continue
            if code >= _OK_CEILING:
                failed.append({"path": path, "status": code})
            else:
                ok.append(path)
            ids = _ids_from(body)
            if ids:
                # Keyed WITHOUT the trailing slash. FastAPI declares some
                # collections as `/projects/` and redirects `/projects` to it,
                # while the ancestor of `/projects/{slug}` is `/projects`. The
                # two spellings have to meet somewhere, and here is cheaper than
                # at every lookup.
                id_pool[path.rstrip("/") or "/"] = ids

        # Phase 2: parameterised routes, ids taken from the longest already
        # probed ancestor collection.
        for path in gets:
            if "{" not in path:
                continue
            why = _skip_reason(path)
            if why:
                skipped.append({"path": path, "why": why})
                continue
            filled = path
            missing = False
            for name in _PARAM_RE.findall(path):
                head = filled.split("{" + name + "}")[0].rstrip("/")
                value = _resolve(name, head, id_pool)
                if value is None:
                    missing = True
                    break
                filled = filled.replace("{" + name + "}", value, 1)
            if missing:
                unprobed.append({"path": path, "why": "no real id for a path parameter"})
                continue
            code, _ = _get(args.base, filled, token, args.timeout)
            if code == 0:
                unprobed.append({"path": path, "why": "timed out or refused"})
            elif code >= _OK_CEILING:
                failed.append({"path": path, "as": filled, "status": code})
            else:
                ok.append(path)
    finally:
        # Revoked AND removed. `revoke_session` only flips the flag, which is
        # right for a real session (a revoked one is evidence) and wrong for
        # this one: the reviewer gate runs on most backend diffs, so leaving a
        # row per run grows the sessions table forever for no reader. The row
        # is this process's own, identified by its own token.
        auth_store.revoke_session(token)
        try:
            with auth_store._connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                             (auth_store._hash_token(token),))
        except Exception:
            pass  # revoked is already the security-relevant half

    result = {
        "base": args.base,
        "get_routes": len(gets),
        "answered": len(ok),
        "failed": failed,
        "skipped": len(skipped),
        "unprobed": len(unprobed),
    }
    if args.json:
        print(json.dumps({**result, "skipped_detail": skipped,
                          "unprobed_detail": unprobed}, indent=1))
    else:
        print(f"check_routes_answer: {len(ok)} of {len(gets)} GET routes answered, "
              f"{len(failed)} returned 5xx, {len(skipped)} skipped, "
              f"{len(unprobed)} not probed")
        if args.list:
            for s in skipped:
                print(f"  skip     {s['path']}  ({s['why']})")
            for u in unprobed:
                print(f"  unprobed {u['path']}  ({u['why']})")
        for f in failed:
            shown = f.get("as", f["path"])
            print(f"  FAIL {f['status']}  {f['path']}"
                  + (f"   as {shown}" if shown != f["path"] else ""))
    if failed:
        print("\ncheck_routes_answer: a 5xx here is usually a response_model that "
              "does not match what the handler returns. The traceback naming the "
              "field is in the backend log:\n"
              "  journalctl --user -u workshop-backend.service --since '5 min ago' "
              "| grep -A5 ResponseValidationError", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
