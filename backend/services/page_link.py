"""The page a chat is open beside, and the relay that lets the chat use it.

A chat can sit in a drawer beside any page of the Workshop: a Workshop screen or
an app's own screen. The page lives in the operator's browser, not here, so
everything the chat learns about it and everything it does to it goes through
the client that shows both:

    drawer  --PUT page-->       this module   (which page, what it declares)
    drawer  --PUT thread link-> this module   (which conversation it shows)
    drawer  <--SSE calls--      this module   (a tool call to run on the page)
    drawer  --POST result-->    this module   (what running it returned)
    workshop tool server --POST call--> this module, waits for the result

The ACCESS LEVEL is the operator's, set per chat in the chat's Access menu and
stored on the thread, so it survives a reload and applies to the next turn:

    send   only what the operator attaches reaches the chat (the default)
    see    the chat may read the page
    use    the chat may also act on the page, alongside the operator
    drive  the same tools, and the chat is in control until the operator
           takes over

The page state itself is held in memory only. It describes a document open in a
browser right now, so it means nothing after a restart, and the client sends it
again the moment its stream reconnects.

Nothing here names an app. An app tells the client what its page is and which
tools it offers (the `workshop/v1/page` message); this module only carries it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field

from services import ownership
from services import stores as _stores

LEVELS = ("send", "see", "use", "drive")
DEFAULT_LEVEL = "send"
READ_LEVELS = ("see", "use", "drive")
ACT_LEVELS = ("use", "drive")
# How the operator sees a chat's work in an app that shows it (`chat.follow`):
# follow (what it opens or acts on comes to the front), show (its work waits
# in the app, nothing comes forward) or hide (it works unseen, kept out of the
# app's lists). The first is the default.
VIEWS = ("follow", "show", "hide")

# A tool call that the page has not answered by then is reported as failed, so
# a closed laptop lid cannot hold a turn open for its whole timeout.
CALL_TIMEOUT_S = 45.0
# How long a drawer whose stream dropped still counts as beside its chat: a
# phone's stream drops when the app pauses and comes back on its own.
GRACE_S = 20.0
# What the page may report about itself, and what one call may return. Both
# end up in the model's context, so both are capped here rather than trusted.
CONTEXT_CAP = 6000
RESULT_CAP = 40000
IMAGE_CAP = 3_000_000
TOOLS_CAP = 24


def clamp_level(level: object) -> str:
    """A known level, else the default. "use" (acting only when asked) was
    folded into full autonomy, "drive", where the operator can step in
    anytime."""
    if level == "use":
        return "drive"
    return level if isinstance(level, str) and level in LEVELS else DEFAULT_LEVEL


@dataclass
class Page:
    """What the client says is beside the chat."""

    app: str | None = None
    label: str = ""
    title: str = ""
    url: str = ""
    context: str = ""
    tools: list[dict] = field(default_factory=list)


@dataclass
class _Link:
    """One drawer, open in one browser tab: its stream, its page, its calls."""

    page: Page | None = None
    queues: list[asyncio.Queue] = field(default_factory=list)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    # When its last stream dropped, while it may still come back (GRACE_S),
    # and the calls made meanwhile, sent when it does.
    lost_at: float | None = None
    waiting: list[dict] = field(default_factory=list)


@dataclass
class _Registry:
    """Everything this module holds, in one place.

    In this process's memory on purpose: it is open streams and calls waiting
    on them, which exist only in the process holding the connection. A second
    worker would need these handed between processes (a pub/sub channel), the
    same as the chat's own turn streams.

    A drawer holds its stream under its own LINK id from the moment it opens, so
    a conversation that does not exist yet (a blank chat about to send its first
    message) can be bound to it in the one request that precedes its first turn.
    """

    links: dict[str, _Link] = field(default_factory=dict)
    bound: dict[str, str] = field(default_factory=dict)    # thread -> link
    owners: dict[str, int] = field(default_factory=dict)   # thread -> its data owner

    def clear(self) -> None:
        for d in (self.links, self.bound, self.owners):
            d.clear()


_REG = _Registry()


def _link(link_id: str) -> _Link:
    link = _REG.links.get(link_id)
    if link is None:
        link = _REG.links[link_id] = _Link()
    return link


def _text(raw: object, cap: int) -> str:
    return str(raw or "").strip()[:cap]


def _tool(raw: object) -> dict | None:
    """One declared tool, checked, or None."""
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), 48)
    if not name or not name.replace("_", "").isalnum():
        return None
    schema = raw.get("input_schema")
    return {
        "name": name,
        "description": _text(raw.get("description"), 600),
        "input_schema": schema if isinstance(schema, dict) else {"type": "object"},
        # A tool that only reads is usable at `see`; anything else needs `use`.
        "acts": raw.get("acts") is not False,
    }


def parse_page(raw: object) -> Page | None:
    """The client's description of the page, capped, or None for no page."""
    if not isinstance(raw, dict):
        return None
    ctx = raw.get("context")
    if not isinstance(ctx, str):
        ctx = json.dumps(ctx, ensure_ascii=False) if ctx else ""
    tools = [t for t in (_tool(x) for x in (raw.get("tools") or [])[:TOOLS_CAP]) if t]
    return Page(
        app=_text(raw.get("app"), 64) or None,
        label=_text(raw.get("label"), 80),
        title=_text(raw.get("title"), 200),
        url=_text(raw.get("url"), 500),
        context=ctx.strip()[:CONTEXT_CAP],
        tools=tools,
    )


def valid_link_id(link_id: str) -> bool:
    return 8 <= len(link_id) <= 64 and link_id.replace("-", "").isalnum()


def set_page(link_id: str, page: Page | None) -> None:
    _link(link_id).page = page


def bind(thread_id: str, link_id: str | None) -> None:
    """Say which drawer this conversation is open in (None: none)."""
    if link_id:
        _REG.bound[thread_id] = link_id
    else:
        _REG.bound.pop(thread_id, None)


def _live(thread_id: str) -> _Link | None:
    """The drawer beside this chat while it shows a page: connected, or
    dropped less than GRACE_S ago (a phone's stream drops and comes back)."""
    link = _REG.links.get(_REG.bound.get(thread_id, ""))
    if link is None or link.page is None:
        return None
    if link.queues or (link.lost_at is not None and time.monotonic() - link.lost_at < GRACE_S):
        return link
    return None


def page(thread_id: str) -> Page | None:
    """The page beside this chat, while its drawer is open to show it."""
    link = _live(thread_id)
    return link.page if link else None


def online(thread_id: str) -> bool:
    return page(thread_id) is not None


# The tools every page answers, by the platform's own reading of a document or
# by the app's declaration of the same name, which replaces it for that page.
STANDARD_READ = ("read_page", "find")
STANDARD_ACT = ("act",)


def tool_acts(thread_id: str, name: str) -> bool:
    """Whether calling `name` on this chat's page changes anything."""
    if name in STANDARD_READ:
        return False
    if name in STANDARD_ACT:
        return True
    p = page(thread_id)
    declared = next((t for t in (p.tools if p else []) if t["name"] == name), None)
    return True if declared is None else declared["acts"]


def tool_names(thread_id: str, level: str) -> list[str]:
    """The page's own tools this level allows."""
    p = page(thread_id)
    if p is None or level not in READ_LEVELS:
        return []
    return [t["name"] for t in p.tools if level in ACT_LEVELS or not t["acts"]]


def unknown_tool(thread_id: str, name: str, level: str) -> str | None:
    """Why `name` is not a tool of the page beside this chat, with the tools it
    does have, or None when it is one (or there is no page to ask)."""
    p = page(thread_id)
    if p is None or name in STANDARD_READ or name in STANDARD_ACT:
        return None
    if any(t["name"] == name for t in p.tools):
        return None
    allowed = set(tool_names(thread_id, level))
    have = [t for t in p.tools if t["name"] in allowed]
    if not have:
        return f"The page beside this chat ({p.label or 'a page'}) offers no tool named {name!r}, and no tools of its own."
    listing = "; ".join(
        f"{t['name']}({', '.join((t['input_schema'].get('properties') or {}).keys())}): "
        f"{t['description'][:120] or 'no description'}" for t in have)
    return f"The page beside this chat offers no tool named {name!r}. Its tools: {listing}"


# --- the stream a drawer holds open -----------------------------------------

def attach(link_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    link = _link(link_id)
    link.queues.append(q)
    link.lost_at = None
    for item in link.waiting:
        q.put_nowait(item)
    link.waiting.clear()
    return q


def detach(link_id: str, q: asyncio.Queue) -> None:
    """A stream closed. With none left the drawer may be gone or only
    reconnecting, so it is kept GRACE_S before it is dropped."""
    link = _REG.links.get(link_id)
    if link is None:
        return
    if q in link.queues:
        link.queues.remove(q)
    if not link.queues:
        link.lost_at = time.monotonic()
        try:
            asyncio.get_running_loop().call_later(GRACE_S, _expire, link_id)
        except RuntimeError:
            _expire(link_id, now=True)


def _expire(link_id: str, now: bool = False) -> None:
    link = _REG.links.get(link_id)
    if link is None or link.queues:
        return
    if not now and (link.lost_at is None or time.monotonic() - link.lost_at < GRACE_S):
        return
    # Nobody came back to run a call, so nothing waiting will be answered.
    for fut in link.pending.values():
        if not fut.done():
            fut.set_result({"ok": False, "error": "The page beside this chat was closed."})
    link.pending.clear()
    _REG.links.pop(link_id, None)
    for thread_id in [t for t, l in _REG.bound.items() if l == link_id]:
        _REG.bound.pop(thread_id, None)


# --- calls from the workshop tool server ------------------------------------

def _key() -> bytes:
    """The installation's key for page and options tokens, kept in the runtime
    root so tokens handed to a turn still hold after a restart adopts it."""
    global _KEY
    if _KEY is None:
        path = _stores._runtime_root() / "page-link.key"
        try:
            _KEY = path.read_bytes()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            _KEY = key
    return _KEY


_KEY: bytes | None = None
_THREAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _signed(kind: str, thread_id: str) -> str:
    mac = hmac.new(_key(), f"{kind}:{thread_id}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{thread_id}.{mac}"


def _thread_of(kind: str, tok: str) -> str | None:
    thread_id, _, _mac = (tok or "").rpartition(".")
    if not _THREAD_RE.match(thread_id):
        return None
    return thread_id if hmac.compare_digest(_signed(kind, thread_id), tok) else None


def token(thread_id: str) -> str:
    """The token a turn's tool server calls the page with: the thread, signed.
    Minted inside the owner's own request, so the owner is remembered for the
    calls, which arrive from loopback with no signed-in user to pin one."""
    owner = ownership.owner_or_none()
    if owner is not None:
        _REG.owners[thread_id] = owner
    return _signed("page", thread_id)


def options_headers(thread_id: str, app_slug: str) -> dict[str, str]:
    """Headers for an app's tool server in a turn of this chat: where to read
    which of its options the chat has on NOW, so one switched on mid-turn
    reaches the app's next call. The token reads nothing else."""
    token(thread_id)
    return {"X-Workshop-Options-Url": f"{backend_base()}/chat-access/{thread_id}/options/{app_slug}",
            "X-Workshop-Options-Token": _signed("options", thread_id)}


def thread_for_options_token(tok: str) -> str | None:
    return _thread_of("options", tok)


def owner_of(thread_id: str) -> int | None:
    """The chat's data owner: as its token was minted, else whose runtime
    holds the chat (after a restart)."""
    known = _REG.owners.get(thread_id)
    if known is not None or not _THREAD_RE.match(thread_id or ""):
        return known
    for found in (_stores._runtime_root() / "users").glob(f"*/chat/{thread_id}"):
        if found.is_dir() and found.parent.parent.name.isdigit():
            _REG.owners[thread_id] = int(found.parent.parent.name)
            return _REG.owners[thread_id]
    return None


def thread_for_token(tok: str) -> str | None:
    return _thread_of("page", tok)


async def call(thread_id: str, tool: str, args: dict,
               access: list[str] | None = None) -> dict:
    """Run `tool` on the page beside this chat and return what it answered:
    `{ok, text}` or `{ok: False, error}`. `access` is which of the page's app's
    own options this chat has on, passed along for the app to obey."""
    link = _live(thread_id)
    if link is None:
        return {"ok": False, "error": "No page is open beside this chat right now."}
    call_id = secrets.token_hex(8)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    link.pending[call_id] = fut
    item = {"id": call_id, "tool": tool, "args": args, "access": access}
    if not link.queues:
        link.waiting.append(item)
    for q in list(link.queues):
        q.put_nowait(item)
    try:
        return await asyncio.wait_for(fut, CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "The page did not answer in time."}
    finally:
        link.pending.pop(call_id, None)


def resolve(link_id: str, call_id: str, answer: dict) -> bool:
    link = _REG.links.get(link_id)
    fut = link.pending.get(call_id) if link else None
    if fut is None or fut.done():
        return False
    fut.set_result(clean_answer(answer))
    return True


def clean_answer(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"ok": False, "error": "The page sent an answer that could not be read."}
    if not raw.get("ok"):
        return {"ok": False, "error": _text(raw.get("error"), 2000) or "The page refused the call."}
    out: dict = {"ok": True, "text": ""}
    result = raw.get("result")
    if isinstance(result, dict):
        out["text"] = _text(result.get("text"), RESULT_CAP)
        image = result.get("image")
        if isinstance(image, str) and image.startswith("data:image/") and len(image) <= IMAGE_CAP:
            out["image"] = image
    elif result is not None:
        out["text"] = (result if isinstance(result, str)
                       else json.dumps(result, ensure_ascii=False))[:RESULT_CAP]
    return out


# --- what a turn's tool server is told --------------------------------------

def backend_base() -> str:
    port = (os.environ.get("WORKSHOP_BACKEND_PORT") or os.environ.get("PORT") or "8000").strip()
    return f"http://127.0.0.1:{port}"


def backend_url() -> str:
    return f"{backend_base()}/page-link/call"


def access_env(thread_id: str | None) -> dict[str, str]:
    """Where a turn's tool server changes this chat's own access (chat_access):
    the same per-chat token as the page calls, which also names the owner."""
    if not thread_id:
        return {}
    return {"WORKSHOP_ACCESS_URL": f"{backend_base()}/chat-access/{thread_id}",
            "WORKSHOP_ACCESS_TOKEN": token(thread_id)}


def mcp_env(thread_id: str | None, level: str) -> dict[str, str]:
    """Environment for the workshop tool server of a turn in this chat: how to
    reach the page and which page tools to offer. Empty when the level lets the
    chat see only what it is sent. Whether a page is beside the chat is asked
    on each call, not here: a drawer that reconnects or opens mid-turn must not
    leave the turn without the tools for it."""
    if not thread_id or level not in READ_LEVELS:
        return {}
    return {
        "WORKSHOP_PAGE_URL": backend_url(),
        "WORKSHOP_PAGE_TOKEN": token(thread_id),
        "WORKSHOP_PAGE_ACCESS": "act" if level in ACT_LEVELS else "read",
    }
