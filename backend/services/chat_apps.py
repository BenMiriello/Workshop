"""Which apps a chat may use, and what a turn gets for each.

Any chat can be given other apps' tools: a chat beside one app's page can also
reach another app, and a chat with no page at all can reach any of them. What an
app offers is its own declaration (the `chat` block `services/app_chat` reads):
its tool server, a one-line `about`, and the `access` options that narrow what
of its data a chat reaches. The operator picks per chat, in the chat's Access
menu; the choice is stored on the thread (`chat_store.get_thread_app_access`).

    {"<app slug>": ["<option id>", ...]}

A key present means the app is granted, and its list is which of the app's
options are on. The chat's OWN app (an `app:<slug>` scope) is always granted,
since it is that app's chat; the map may still carry its options, and when it
does not, the app's declared defaults apply.

The options travel to the app's server as `X-Workshop-Access: a,b`, so the app
enforces them; the platform only carries the choice. Nothing here names an app.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from services import app_chat, chat_store, page_link


@dataclass(frozen=True)
class Grant:
    decl: app_chat.ChatDeclaration
    options: tuple[str, ...]
    own: bool


def grantable() -> list[app_chat.ChatDeclaration]:
    """Every app with a tool server a chat could be given."""
    return [d for d in app_chat.declarations() if d.mcp_service]


def defaults(decl: app_chat.ChatDeclaration) -> list[str]:
    return [o["id"] for o in decl.access if o.get("default", True)]


def normalize(raw: object) -> dict[str, list[str]]:
    """The stored form of a grant map: known apps only, known options only."""
    if not isinstance(raw, dict):
        raise ValueError("app access must be an object of app slug to option ids")
    known = {d.app_slug: d for d in grantable()}
    out: dict[str, list[str]] = {}
    for slug, opts in raw.items():
        decl = known.get(str(slug))
        if decl is None or not isinstance(opts, list):
            continue
        ids = {o["id"] for o in decl.access}
        out[decl.app_slug] = [o for o in dict.fromkeys(str(x) for x in opts) if o in ids]
    return out


def grants(access: dict[str, list[str]], own_slug: str | None) -> list[Grant]:
    """The apps a turn in this chat reaches, its own app first."""
    known = {d.app_slug: d for d in grantable()}
    out: list[Grant] = []
    if own_slug and own_slug in known:
        decl = known[own_slug]
        opts = access.get(own_slug)
        out.append(Grant(decl, tuple(defaults(decl) if opts is None else opts), True))
    for slug, opts in access.items():
        if slug != own_slug and slug in known:
            out.append(Grant(known[slug], tuple(opts), False))
    return out


def _chat_headers(thread_id: str, app_slug: str) -> dict[str, str]:
    """Which chat is calling and its title as the turn starts (URL-quoted, so
    an app can name what it keeps for that chat), whether the app shows what
    it does there (`X-Workshop-Follow`), and where to read that and the
    options as they are now."""
    view = chat_store.get_thread_page_view(str(thread_id))
    return {"X-Workshop-Thread": str(thread_id),
            "X-Workshop-Thread-Title": quote(chat_store.get_thread_title(str(thread_id))[:120], safe=" "),
            "X-Workshop-Follow": "on" if view == "follow" else "off",
            "X-Workshop-View": view,
            **page_link.options_headers(str(thread_id), app_slug)}


def _spec(grant: Grant, thread_id: str) -> dict | None:
    decl = grant.decl
    port = app_chat.service_port(decl.project, decl.mcp_service or "")
    if port is None:
        return None
    spec: dict = {
        "type": "http",
        "url": f"http://127.0.0.1:{port}{decl.mcp_path}",
        "headers": {"X-Workshop-Access": ",".join(grant.options),
                    **_chat_headers(thread_id, decl.app_slug)},
    }
    if decl.tool_timeout_s:
        spec["timeout"] = int(decl.tool_timeout_s * 1000)
    return spec


def turn_servers(thread_id: str, own_slug: str | None) -> tuple[dict[str, dict], list[str]]:
    """(launch specs, allowed tool patterns) for the OTHER apps this chat was
    given. The chat's own app is already on the turn through its provider."""
    access = chat_store.get_thread_app_access(thread_id)
    specs: dict[str, dict] = {}
    allowed: list[str] = []
    for grant in grants(access, own_slug):
        if grant.own:
            continue
        spec = _spec(grant, thread_id)
        if spec is None:
            continue
        specs[grant.decl.server_name] = spec
        allowed.append(f"mcp__{grant.decl.server_name}__*")
    return specs, allowed


def own_access_header(thread_id: str, own_slug: str | None) -> dict[str, str]:
    """The headers the chat's own app server is sent: the same as a granted
    app's, so its options apply and it knows which chat is calling."""
    if not own_slug:
        return {}
    for grant in grants(chat_store.get_thread_app_access(thread_id), own_slug):
        if grant.own:
            headers = _chat_headers(thread_id, own_slug)
            if grant.decl.access:
                headers["X-Workshop-Access"] = ",".join(grant.options)
            return headers
    return {}


def options_for(thread_id: str, app_slug: str) -> list[str] | None:
    """Which of `app_slug`'s options this chat has on: its grant when it has
    one, else the app's declared defaults. None for an app with no options."""
    decl = next((d for d in grantable() if d.app_slug == app_slug), None)
    if decl is None or not decl.access:
        return None
    opts = chat_store.get_thread_app_access(thread_id).get(app_slug)
    return list(defaults(decl) if opts is None else opts)


def describe(thread_id: str, own_slug: str | None) -> list[dict]:
    """What the chat is told about each app it reaches: label, about, tool
    prefix, and which options are on and off."""
    out = []
    for grant in grants(chat_store.get_thread_app_access(thread_id), own_slug):
        decl = grant.decl
        on = [o["label"] for o in decl.access if o["id"] in grant.options]
        off = [o["label"] for o in decl.access if o["id"] not in grant.options]
        out.append({"label": decl.label, "about": decl.about, "own": grant.own,
                    "prefix": f"mcp__{decl.server_name}__", "on": on, "off": off})
    return out


def catalog() -> list[dict]:
    """Every grantable app, for the Access menu."""
    return [{"slug": d.app_slug, "label": d.label, "about": d.about,
             "access": [dict(o) for o in d.access], "follow": d.follow} for d in grantable()]
