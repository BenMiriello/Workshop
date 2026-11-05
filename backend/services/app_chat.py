"""A runner app's chat, declared in the app's own manifest.

An app served by the runner says what its chat is in a `chat` block of its
`workshop.app.json`, and the platform reads that block for the scope
`app:<slug>`. Nothing here names an app: an app that declares the block gets a
chat with its own persona, its own tool server and its own default model, and
an app that does not keeps whatever `chat_context.get_provider` gave it before.

    "chat": {
      "persona": "<file in the app's tree>",
      "mcp": {"service": "<service name>", "path": "/mcp", "timeout_s": 900},
      "model": "<model id or tier>",
      "about": "<one line: what the app's tools reach>",
      "options": [{"id": "<option>", "label": "<what it reaches>",
                   "default": true}],
      "gateway": {"service": "<service name>", "path": "/llm",
                  "turn_end": "/turn-ended", "efforts": ["low", "high"],
                  "effort_default": "low", "app": "<another app's slug>"}
    }

Every key is optional. `persona` is read each time a prompt is assembled, so
editing the file needs no restart. `mcp.service` names one of the
manifest's own services; its server is attached over HTTP at the port that
service runs on, resolved the way the runner's proxy resolves it, and every
tool it lists is allowed. `mcp.timeout_s` is how long one call to that
server may take before the CLI gives up on it (the CLI's own limit is a
minute). `model` is the scope's DECLARED default, rung 4 of
`services/model_resolution` (which also turns a tier name into a model id), so a
thread pin or an explicit pick still wins and a model that cannot be reached is
skipped with a named reason.

`about` and `options` are read when ANOTHER chat is given this app's tools
(`services/chat_apps`): `about` is the line that chat is told about the app,
and `options` lists the parts of the app's data the operator can switch on or
off for it, each sent to the app's server as `X-Workshop-Access` so the app
itself enforces them.

`gateway` names one of the app's services that a turn's model requests go
through instead of straight to the model server, so the app can hold, order
and show them as its own work (`services/app_chat_gateway`). `gateway.app`
names another app whose service that is instead: an app with no queue of its
own lets the app that queues work on the same model hold its chat's requests.

`gateway.turn_end` names a path under the gateway's own path that is told
when each turn ends and how (`services/app_chat_turns`). `gateway.efforts`
lists the effort levels the gateway honours: a chat in the scope on a model the
gateway serves offers that ladder, whatever the model itself declares
(`model_caps.efforts_for`), and every turn there names its level, the
`effort_default` (else the ladder's middle rung) when nothing was picked.

The persona is L2. The platform kernel and orientation still come first and the
operator's own instructions for the scope still come after it, exactly as for
any other scope.

This module only READS the block. The provider built from it is
`chat_context.AppChatProvider`, so that this module stays outside the import
cycle `chat_context` sits in: it depends on the app runner and nothing that
depends back on it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from services import app_links, app_runner

log = logging.getLogger("workshop.app_chat")

__all__ = ["ChatDeclaration", "declaration", "declarations", "service_port"]

DEFAULT_MCP_PATH = "/mcp"
DEFAULT_GATEWAY_PATH = "/llm"

# What the CLI accepts as an MCP server name, which is also the middle segment
# of every tool id the server contributes (`mcp__<server>__<tool>`).
_SERVER_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ChatDeclaration:
    """One app's `chat` block, read and checked."""

    app_slug: str
    project: str
    root: Path
    label: str
    persona_file: Path | None
    mcp_service: str | None
    mcp_path: str
    model: str | None
    gateway_service: str | None = None
    #: The project whose service hosts the gateway: this app's own unless the
    #: declaration names another app (`gateway.app`), so an app's chat can be
    #: queued by the app that arbitrates what it runs on.
    gateway_project: str | None = None
    gateway_path: str = DEFAULT_GATEWAY_PATH
    tool_timeout_s: float | None = None
    gateway_turn_end: str | None = None
    gateway_efforts: tuple[str, ...] = ()
    gateway_effort_default: str | None = None
    about: str = ""
    access: tuple[dict, ...] = ()
    # The app shows what a chat does in it while the operator follows the chat
    # (`chat.follow`), so the chat offers the Follow or stay switch.
    follow: bool = False
    # Where this app says a chat is working in it (`chat.place`, a path on the
    # MCP service with `{thread}`): what the chat's header offers to go to.
    place: str | None = None

    @property
    def server_name(self) -> str:
        return _SERVER_NAME_RE.sub("_", self.app_slug).strip("_") or "app"


def _contained(root: Path, rel: object) -> Path | None:
    """`rel` under `root`, or None when it is absent or leaves the tree."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    base = root.resolve()
    target = (base / rel.strip()).resolve()
    if target != base and base not in target.parents:
        log.warning("app chat: persona %r is outside the app's tree; ignored", rel)
        return None
    return target


def _path(raw: object, default: str | None) -> str | None:
    text = str(raw or "").strip() or default
    if text is None:
        return None
    return text if text.startswith("/") else f"/{text}"


def _seconds(raw: object) -> float | None:
    """A positive number of seconds, or None."""
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _levels(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    words = (str(x).strip().lower() for x in raw if isinstance(x, str))
    return tuple(dict.fromkeys(w for w in words if w))


def _access(raw: object) -> tuple[dict, ...]:
    """The declared access options, each `{id, label, default}`."""
    if not isinstance(raw, list):
        return ()
    out: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "").strip()
        if not oid or oid in out:
            continue
        out[oid] = {"id": oid, "label": str(item.get("label") or oid).strip()[:80],
                    "default": item.get("default") is not False}
    return tuple(out.values())


def declaration(app_slug: str) -> ChatDeclaration | None:
    """The app's `chat` block, or None when it declares none.

    None also for an app with no working tree, for the installation's own
    source, and for a manifest that cannot be read: a chat that cannot be
    described falls back to the platform's answer rather than failing.
    """
    try:
        project = app_links.linked_project_slug(app_slug)
        if not project or app_runner.is_self_project(project):
            return None
        root = app_runner._project_dir(project)
        raw = app_runner._read_manifest_file(root)
        record = app_links.load().projects.get(project) or {}
    except Exception as e:
        log.debug("app chat: no declaration readable for %r: %s", app_slug, e)
        return None
    block = raw.get("chat") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return None
    mcp = block.get("mcp") if isinstance(block.get("mcp"), dict) else {}
    service = str(mcp.get("service") or "").strip() or None
    model = str(block.get("model") or "").strip()
    gw = block.get("gateway") if isinstance(block.get("gateway"), dict) else {}
    return ChatDeclaration(
        app_slug=app_slug,
        project=project,
        root=root,
        label=str(record.get("title") or app_slug),
        persona_file=_contained(root, block.get("persona")),
        mcp_service=service,
        mcp_path=_path(mcp.get("path"), DEFAULT_MCP_PATH),
        model=model or None,
        gateway_service=str(gw.get("service") or "").strip() or None,
        gateway_project=_gateway_project(gw.get("app"), project),
        gateway_path=_path(gw.get("path"), DEFAULT_GATEWAY_PATH),
        tool_timeout_s=_seconds(mcp.get("timeout_s")),
        gateway_turn_end=_path(gw.get("turn_end"), None),
        gateway_efforts=_levels(gw.get("efforts")),
        gateway_effort_default=str(gw.get("effort_default") or "").strip().lower() or None,
        about=str(block.get("about") or "").strip()[:600],
        access=_access(block.get("options")),
        follow=block.get("follow") is True,
        place=_path(block.get("place"), None) if "{thread}" in str(block.get("place") or "") else None,
    )


def _gateway_project(app: object, own: str) -> str:
    """The project a `gateway.app` names, or this app's own when it names
    none or one that is not linked to a project."""
    named = str(app or "").strip()
    if not named:
        return own
    try:
        return app_links.linked_project_slug(named) or own
    except Exception as e:  # noqa: BLE001 - an unreadable link is no gateway move
        log.debug("app chat: gateway app %r not resolved: %s", named, e)
        return own


def service_port(project: str, service: str) -> int | None:
    """The port `service` of `project` answers on, or None.

    The running port first, read from the same status the runner's proxy reads,
    then the port the manifest pins, which is where a pinned service will be
    once it is up again. None means nothing says where it is.
    """
    try:
        status = app_runner.app_status(project)
    except Exception as e:
        log.warning("app chat: status of %r unavailable: %s", project, e)
        status = {}
    for svc in status.get("services") or []:
        if (svc.get("name") == service and svc.get("port")
                and svc.get("status") == "running"):
            return int(svc["port"])
    try:
        specs = app_runner.resolve_manifest(project).services
    except Exception as e:
        log.warning("app chat: manifest of %r unavailable: %s", project, e)
        return None
    return next((s.port for s in specs if s.name == service and s.port), None)


def declarations() -> list[ChatDeclaration]:
    """Every app that declares a chat, in roster order."""
    try:
        slugs = app_links.all_app_slugs()
    except Exception as e:
        log.warning("app chat: roster unreadable: %s", e)
        return []
    return [d for d in (declaration(slug) for slug in slugs) if d is not None]
