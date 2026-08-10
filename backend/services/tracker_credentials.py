"""Where a tracker token comes from. The seam, not the token.

The adapter takes a token and never asks how it was obtained. That is the whole
point: the same adapter serves a single-operator box and a signed-up user, because
only this module changes between them.

Three sources, in the order they were decided (§12 of
ticket-model-and-external-trackers-2026-08-07):

  gh           the local `gh` CLI's own auth. Zero setup, works today, and does
               NOT survive to a multi-user product — it is one person's
               long-lived token in one home directory. Fine as the local
               implementation of the seam; never the design.
  device_flow  OAuth 2.0 device authorization grant. No callback URL at any
               point, so it works on a headless box AND in a hosted deployment
               with one code path. Needs a registered GitHub App's client_id,
               which only the operator can create on github.com — so this is
               wired to the point of that missing value and no further.
  pat          a token pasted into Settings. The documented escape hatch.

Tokens live in `secrets_store` under `tracker:<provider>`, never in the binding
record.
"""
from __future__ import annotations

import os

from services import secrets_store

# The scopes each provider needs, so a Settings screen can say what it is asking
# for rather than showing an opaque consent page.
REQUIRED_SCOPES = {
    "github": ("repo",),          # issues read/write ride on `repo`
    "linear": ("read", "write"),
    "jira": ("read:jira-work", "write:jira-work", "offline_access"),
}

SOURCES = ("gh", "device_flow", "pat")


def _slug(provider: str) -> str:
    return f"tracker:{provider}"


def token_for(provider: str, source: str) -> str | None:
    """The bearer token, or None when the source supplies it out of band."""
    if source == "gh":
        # `gh` injects its own credential; there is no token for us to hold.
        return None
    if source in ("pat", "device_flow"):
        return (secrets_store.load(_slug(provider)) or {}).get("token") or None
    raise ValueError(f"unknown credential source: {source!r}")


def set_token(provider: str, token: str) -> None:
    secrets_store.set_values(_slug(provider), {"token": token.strip()})


def clear(provider: str) -> None:
    secrets_store.delete_key(_slug(provider), "token")


def env_for(binding: dict) -> dict[str, str]:
    """Environment for a `gh` invocation that must use OUR token rather than the
    CLI's own login. `GH_TOKEN` is what `gh` reads, so a device-flow or pasted
    token flows through the same adapter with no branching in it."""
    provider = str(binding.get("provider") or "")
    source = str(binding.get("credential_source") or "gh")
    token = token_for(provider, source)
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
    return env


def status(provider: str, source: str) -> dict:
    """What Settings needs to render: is this usable, and if not, what is missing.

    Honest about device_flow: it is designed and wired to the seam, and it cannot
    work until a GitHub App exists, which is an action on github.com that cannot
    be performed from this box.
    """
    if source == "gh":
        return {"source": "gh", "ready": _gh_logged_in(),
                "detail": "uses the local gh CLI login",
                "scopes": list(REQUIRED_SCOPES.get(provider, ()))}
    if source == "pat":
        has = bool(token_for(provider, "pat"))
        return {"source": "pat", "ready": has,
                "detail": "a token pasted into Settings" if has else "no token stored",
                "scopes": list(REQUIRED_SCOPES.get(provider, ()))}
    if source == "device_flow":
        client_id = os.environ.get(f"WORKSHOP_{provider.upper()}_CLIENT_ID")
        if not client_id:
            return {"source": "device_flow", "ready": False,
                    "detail": ("needs a registered GitHub App client id in "
                               f"WORKSHOP_{provider.upper()}_CLIENT_ID; register the "
                               "App on github.com, which cannot be done from here"),
                    "scopes": list(REQUIRED_SCOPES.get(provider, ()))}
        return {"source": "device_flow", "ready": bool(token_for(provider, "device_flow")),
                "detail": "device authorization grant",
                "scopes": list(REQUIRED_SCOPES.get(provider, ()))}
    return {"source": source, "ready": False, "detail": "unknown source", "scopes": []}


def _gh_logged_in() -> bool:
    import subprocess
    try:
        res = subprocess.run(["gh", "auth", "status"], capture_output=True,
                             text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0
