#!/usr/bin/env python3
"""Mint a service token for an app's own server, straight into that app's env.

A runner app's service receives the values its manifest declares in
`env_requirements`, from the app's store in `services/secrets_store.py` (the
store the Environment panel fills). An in-process app declares its tokens on
`AppSpec.service_tokens` and `migrate_to_owned.py` mints those; this is the same
mint, `auth_store.create_service_token`, for an app whose server runs as its own
process and needs to call the platform's API:

    cd backend && .venv/bin/python scripts/mint_app_token.py <slug> <ENV_KEY> <scope> [--name NAME]

The token is bound to the operator and to `scope`; what the scope allows is the
called route's rule, not this script's. `--name` is the label the token is known
by (who a route records as having made a change); it defaults to the key.

Never rotates: when the app's store already holds ENV_KEY, nothing is minted.
The raw token goes only into the store, never to stdout. The service sees it on
its next start.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from services import auth_store, secrets_store  # noqa: E402


def _declared(slug: str, key: str) -> bool | None:
    """Whether the app's manifest declares `key`; None when it cannot be read."""
    from services import app_runner
    try:
        return any(r.key == key for r in app_runner.resolve_manifest(slug).env_requirements)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("key")
    ap.add_argument("scope")
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    if secrets_store.status(a.slug, [a.key]).get(a.key):
        print(f"{a.slug}: {a.key} is already set; nothing minted")
        return 0
    owner = auth_store.operator_id()
    if owner is None:
        print("no operator account; run migrate_to_owned.py first", file=sys.stderr)
        return 2
    raw = auth_store.create_service_token(owner, a.scope, label=a.name or a.key)
    secrets_store.set_values(a.slug, {a.key: raw})
    print(f"{a.slug}: minted {a.key} (scope {a.scope!r}, name {a.name or a.key!r}) into its env")
    if _declared(a.slug, a.key) is False:
        print(f"  note: {a.slug}'s manifest does not declare {a.key} in env_requirements,"
              " so its services will not receive it until it does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
