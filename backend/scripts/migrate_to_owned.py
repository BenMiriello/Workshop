#!/usr/bin/env python3
"""One-time, idempotent bootstrap for the auth + ownership redesign.

This is the ONLY place the operator's email is consulted to assign ownership;
after it runs, nothing at request time branches on "is this the operator". Run
once on the deployment box (re-running is a no-op):

    cd backend && .venv/bin/python scripts/migrate_to_owned.py

It (1) flags the operator account (by WORKSHOP_OPERATOR_EMAIL) as the admin and
the data owner of every single-dataset domain the INSTALLED apps declare, and
(2) mints the operator-owned service / ingest tokens those apps declare, writing
them to ~/.config/workshop/secrets.env (0600).

THIS FILE NAMES NO APP, and it used to name four. `_DOMAINS` was a literal list
of one operator's app ids, `_FILE_MOVES` was a literal list of that operator's
app data files, and `_TOKENS` was one app's automation credential. All three
were platform source carrying facts only an app can know, so all three moved
onto the declaration an app already fills in: `AppSpec.owned_domains` and
`AppSpec.service_tokens`. An app that is not installed contributes nothing here,
which is the right answer rather than a special case.

The per-user file relocation this script used to perform is GONE rather than
generalized. It moved a fixed set of flat files out of `.runtime/` and into
`.runtime/users/<owner_id>/` on the one deployment that predates owner
partitioning. Every app declares its own per-owner stores now
(`AppSpec.stores`, `per_owner=True`), a fresh installation writes them in the
partitioned location from the first request, and there is nothing to move.

No WORKSHOP_SESSION_SECRET is needed: sessions are opaque server-side tokens, not
signed cookies, so there is no signing secret to manage.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from services import app_registry, auth_store  # noqa: E402

SECRETS = Path(
    os.environ.get("WORKSHOP_SECRETS_ENV", str(Path.home() / ".config/workshop/secrets.env"))
)


def _log(msg: str) -> None:
    print(f"[migrate] {msg}")


def ensure_operator() -> int:
    email = os.environ.get("WORKSHOP_OPERATOR_EMAIL", "").strip()
    if not email:
        _log("ERROR: WORKSHOP_OPERATOR_EMAIL is not set; cannot identify the operator.")
        sys.exit(2)
    user = auth_store.get_by_email(email)
    if user is None:
        _log(f"ERROR: no account for {email}. Register it first (POST /auth/register), then re-run.")
        sys.exit(2)
    auth_store.set_operator(user["id"])
    _log(f"operator = {email} (id={user['id']}), is_operator set")
    return user["id"]


def assign_domains(owner_id: int, specs: list) -> None:
    domains = sorted({d for spec in specs for d in getattr(spec, "owned_domains", ())})
    if not domains:
        _log("no installed app declares an owned domain; nothing to assign")
        return
    for d in domains:
        if auth_store.domain_owner(d) == owner_id:
            continue
        auth_store.set_domain_owner(d, owner_id)
        _log(f"domain '{d}' -> owner {owner_id}")


def _read_secrets() -> str:
    try:
        return SECRETS.read_text()
    except OSError:
        return ""


def mint_tokens(owner_id: int, specs: list) -> None:
    wanted: list[tuple[str, str]] = []
    for spec in specs:
        for env_name, scope in getattr(spec, "service_tokens", ()):
            if (env_name, scope) not in wanted:
                wanted.append((env_name, scope))
    if not wanted:
        _log("no installed app declares a service token; nothing to mint")
        return

    existing = _read_secrets()
    lines: list[str] = []
    for env_name, scope in wanted:
        if f"{env_name}=" in existing:
            continue  # already minted; don't rotate on re-run
        raw = auth_store.create_service_token(owner_id, scope, label=env_name)
        lines.append(f"{env_name}={raw}")
        _log(f"minted {env_name} (scope {scope})")
    if not lines:
        _log("service tokens already present; nothing minted")
        return
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    body = existing
    if body and not body.endswith("\n"):
        body += "\n"
    body += "# auth-redesign machine tokens (operator-owned)\n" + "\n".join(lines) + "\n"
    SECRETS.write_text(body)
    os.chmod(SECRETS, 0o600)
    _log(f"wrote {len(lines)} token(s) to {SECRETS}")


def main() -> None:
    auth_store.init_db()
    owner_id = ensure_operator()
    # `installed_specs` already drops an app whose declaration failed to
    # build (the catalog logs and skips it), so a broken app cannot stop the
    # operator being flagged -- the one step here that nothing else does.
    specs = app_registry.installed_specs()
    assign_domains(owner_id, specs)
    mint_tokens(owner_id, specs)
    _log("done. Re-running is a no-op.")


if __name__ == "__main__":
    main()
