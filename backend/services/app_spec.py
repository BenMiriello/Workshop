"""The app declaration contract — the types an app fills in to be an app.

THIS MODULE NAMES NO APP, and that is its whole reason for existing. It used to
live inside `app_registry.py` alongside eight hardcoded per-app factories, so
the file that defines what an app IS was also the file that enumerated which
apps EXIST. An app could not declare itself without editing platform source,
which is the thing the platform is supposed to make unnecessary.

The split is: the CONTRACT is platform code (here), the DECLARATIONS are app
code (each app's own `services/<app>_app.py`, or an out-of-tree distribution's
`workshop.apps` entry point). `app_registry.py` discovers declarations and
never names one.

The two store-path helpers sit here rather than in the registry for the same
reason: an app declaring a store needs them, and reaching back into the
registry for them would make every app module import the discoverer that
imports it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

__all__ = ["AppSpec", "RouterMount", "owner_file", "runtime_dir"]


@dataclass(frozen=True)
class RouterMount:
    """One FastAPI router an app contributes, with its prefix/tags/auth."""
    router: Any
    prefix: str = ""
    tags: tuple[str, ...] = ()
    deps: tuple = ()  # FastAPI Depends() dependencies for the mount


@dataclass(frozen=True)
class AppSpec:
    id: str
    card: dict[str, Any] | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    mounts: tuple[RouterMount, ...] = ()
    mcp: dict[str, dict] = field(default_factory=dict)
    # Provider interface for each MCP server this app registers (Phase 4). The
    # `mcp` field carries the LAUNCH spec (type/command/args) consumed by the chat
    # tool-use router; `mcp_provider` carries the PROVIDER-INTERFACE ops, kept
    # separate so adding ops never perturbs the launch-spec path. Every provider
    # MUST declare an `export` op naming the Store it dumps:
    #   {"<server>": {"export": {"store_uri": "workshop://store/<app>/<name>"}}}
    # See mcp_provider_export() below.
    mcp_provider: dict[str, dict] = field(default_factory=dict)
    on_startup: tuple[Callable[[FastAPI], Awaitable[None]], ...] = ()
    background: tuple[Callable[[], Awaitable[None]], ...] = ()
    # Chat surface (services/chat_context, services/app_persona):
    #   chat_provider — factory returning this app's ContextProvider (its in-app
    #                   assistant persona). Exposed only when the app is installed.
    #   chat_snapshot — a compact live-state string for the GENERAL assistant's
    #                   cross-app snapshot. None => this app contributes none.
    chat_provider: Callable[[], Any] | None = None
    chat_snapshot: Callable[[], str] | None = None
    # Model tier for this app's in-app assistant. None => the platform default.
    # Here rather than in routes/chat.py so opting an app up to a bigger model
    # is a property of the app, not a line the platform keeps about it.
    chat_model_tier: str | None = None
    # Event-bus sources this app accepts webhooks from (routes/events.py unions
    # these with the platform's own over the installed set).
    event_sources: tuple[str, ...] = ()
    # Webhook signature verifiers, keyed by the source they verify. Signature:
    #   (headers: dict[str, str], body: bytes) -> (ok: bool, reason: str)
    # with `headers` lowercased. A source with no verifier is accepted with
    # reason "no_verifier_registered", which is why the platform must not be the
    # place this lives: whether a webhook is authentic is knowledge the app that
    # owns the integration has, and the platform's default is permissive.
    # Declared lazily (the factory can reference an app module) and read per
    # request, so an app can be installed without restarting the receiver.
    event_verifiers: dict[str, Callable[[dict, bytes], tuple[bool, str]]] = field(default_factory=dict)
    # Cross-arrival idempotency, keyed by source. Signature:
    #   (event_summary: dict) -> str | None
    # Return a key that is stable across redeliveries of the SAME event (Plaid
    # retries identical webhooks), or None to fall back to the platform's
    # content hash. Only the app knows which payload fields identify an event
    # rather than merely varying with it.
    event_dedup: dict[str, Callable[[dict], str | None]] = field(default_factory=dict)
    # Store adapters this app contributes, keyed by the Store `type` string it
    # shapes. `stores` above declares that a store of type T exists; this is the
    # thing that knows how to select and project rows out of it.
    store_adapters: tuple[Any, ...] = ()
    # Semantic-index collectors this app contributes: kind -> () -> list[Chunk].
    # `default` is False for a corpus the operator opts into rather than one
    # indexed on every build (semantic_index.DEFAULT_KINDS).
    index_collectors: dict[str, Callable[[], Any]] = field(default_factory=dict)
    index_collectors_default: tuple[str, ...] = ()
    # An app's TOOLS are declared in its manifest, not here: see
    # `services/tool_registry.app_tools` and
    # `docs/platform-redesign/tools-are-declared-2026-08-23.md`. The `tools`
    # field that used to sit here fed `routes/agents.known_tools`, in the same
    # made-up vocabulary as that route's deleted five-string platform list --
    # not CLI tool names, not MCP tool names, and enforced by nothing.
    # Stores this app owns: (name, path_factory, type). The factory is called
    # lazily so a store whose directory does not exist yet costs nothing at
    # import time. `per_owner` stores resolve under the owner's runtime dir.
    stores: tuple[tuple[str, Callable[[], Any], str, bool], ...] = ()
    # The owner segment of this app's store URIs, when it is not the app id.
    #
    # It exists for exactly one reason: an app whose stores were declared in
    # platform code BEFORE they were declared here, under a URI that is already
    # written down elsewhere. Moving such a store onto its AppSpec is the fix
    # (a store must exist exactly while the app that owns it is installed);
    # renaming its URI at the same time is a separate, breaking change that
    # nothing here needs. So the URI is pinned and the ownership moves.
    # Empty means the app id, which is what a new app should always use.
    store_owner: str = ""
    # Paths the PUBLIC edge must NEVER forward to this app: unauthenticated or
    # loopback-trusted ingest that would otherwise inherit the proxy's trust.
    # `_exact` is for an ingest POST whose siblings under the same prefix are
    # ordinary authed reads the SPA uses (workshop_public_app.py).
    edge_blocked_prefixes: tuple[str, ...] = ()
    edge_blocked_exact: tuple[str, ...] = ()
    # Env vars to strip from a spawned agent turn because they are this app's
    # secrets and nothing downstream should inherit them (llm_router).
    secret_env: tuple[str, ...] = ()
    # Processes this app expects to be running, probed by routes/services.py.
    # Each entry: (name, category, url, probe_path, expect_status). An app that
    # runs out of process is the only thing that knows what "up" means for it.
    service_probes: tuple[tuple[str, str, str, str, tuple[int, ...]], ...] = ()


# --------------------------------------------------------------------------- #
# Store path helpers, for an app declaring `stores`.
#
# Imported at MODULE SCOPE. `services.stores` has no module-level service
# imports at all -- it reaches the registry only from inside two functions --
# so there is no import-time cycle to dodge here, and a function-local import
# would only add two more to a count the layout gate already tracks.
# --------------------------------------------------------------------------- #
from services import stores as _stores  # noqa: E402


def runtime_dir(name: str):
    """An app-shared (not per-owner) directory under the runtime root."""
    return _stores._runtime_root() / name


def owner_file(name: str):
    """A per-owner data file under the current owner's runtime dir."""
    base = _stores._operator_runtime_dir()
    return None if base is None else base / name
