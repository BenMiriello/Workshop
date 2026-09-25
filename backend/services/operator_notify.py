"""Reaching the operator when they are not looking at the app.

WHAT WAS MISSING. A chat could do work that outlives the turn
(`spawn_background`) and could arrange to be woken at a time (`services/wakeups`),
but everything it produced landed in the app and waited to be read. If the
operator asked for something and walked away, the answer sat in a thread nobody
was looking at. Jobs and shell scripts had a way out -- they append to the
operator notify file -- and a chat had none.

WHY THIS DOES NOT USE THE NOTIFY FILE. Measured 2026-09-01: nothing reads it.
`WORKSHOP_NOTIFY_FILE` has four writers (`services/scoped_exec`,
an app's alerter, and the two report scripts) and no reader anywhere --
not in this repo, not on the host, not in any timer or unit. The file names a
"chat bridge" that was to tail it; that bridge is gone from this host, so every
line written since has gone into a drop nobody empties. That alerter's own
delivery log records `{"queued": true, "via": "notify-file"}` for its last alerts,
which is that path reporting, accurately, that it did not deliver. Appending to
it here would have produced a tool that always succeeds and never arrives, which
is worse than not having one: an assistant would tell the operator it had sent
word, truthfully as far as it could tell, and no word would come.

So this sends over the messaging channel the installation actually has, and
returns the transport's own answer -- a message id on success, the failure
otherwise. A notification the platform could not deliver is reported as
undelivered to the chat that asked for it.

WHAT IT IS FOR. One thing the operator asked for, now finished or now stuck. It
is not a log, not a progress ticker, and not a way to say hello: every message
costs the operator an interruption wherever they are, which is exactly the value
it has and exactly the thing that spending it carelessly destroys.

WHERE IT GOES. A direct message to the operator, not a room, because a room can
be muted and the whole point is to reach a person who is elsewhere. The account
to reach is an installation fact (`installation.operator_dm_target`), declared in
the instance config and never written into prompt text or into this file.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote

import httpx

from services import installation

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"

# The bot credential, read from the environment the backend was started with.
# Absent means this installation has no messaging channel configured, which is a
# state to REPORT rather than to work around -- see `configured()`.
_TOKEN_VAR = "DISCORD_BOT_TOKEN"


# ...and the file the installation may keep it in instead. Declared with
# WORKSHOP_DISCORD_ENV, which existed and was READ BY NOBODY: measured
# 2026-09-01, the only mention of it in the tree was a commented line in the
# instance-config example. So an installation could name its credential file,
# have a valid credential in it, and still have every notification fall back to
# the file nothing reads -- which is exactly what that app alerter's delivery
# log recorded, `{"queued": true, "via": "notify-file"}`, for every alert it
# ever sent. The variable is a declaration; something has to honour it.
_TOKEN_FILE_VAR = "WORKSHOP_DISCORD_ENV"


def _token_from_file() -> str:
    """The credential out of the file the installation declared, or empty.

    Never raises and never logs the value: an unreadable or absent file means
    this installation has no credential, which is a state `configured()` reports
    rather than an error anybody needs to see.
    """
    path = (os.environ.get(_TOKEN_FILE_VAR) or "").strip()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith(f"{_TOKEN_VAR}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _token() -> str:
    """The credential, from the environment or from the declared file.

    The environment wins when it carries a real value, but an EMPTY variable is
    not a value -- and empty is exactly how this one arrives here, which is the
    detail that made the difference between a working channel and a silent one.
    """
    return (os.environ.get(_TOKEN_VAR) or "").strip() or _token_from_file()


def configured() -> bool:
    """Can this installation reach the operator at all?

    Both halves are required: a credential to send with and an account to send
    to. False is the honest answer for an installation that has declared neither,
    and it is what the tool grant and the prompt layers are computed from -- a
    chat is told it can reach the operator only where that is true.
    """
    return bool(_token() and installation.operator_dm_target())


def _dm_channel(client, target: str) -> str:
    """The direct-message channel with the operator, opened if it does not exist.

    Idempotent by the API's own contract: opening a DM that is already open
    returns the same channel, so this holds no cache that could go stale.
    """
    r = client.post(
        f"{API}/users/@me/channels",
        headers={"Authorization": f"Bot {_token()}",
                 "Content-Type": "application/json"},
        json={"recipient_id": target},
    )
    if r.status_code >= 300:
        raise RuntimeError(f"could not open a direct message: http {r.status_code}")
    channel_id = (r.json() or {}).get("id")
    if not channel_id:
        raise RuntimeError("the messaging channel returned no conversation id")
    return str(channel_id)


def origin() -> str:
    """The address the operator opens this installation at, or empty.

    One line, because the preference order is an installation fact and lives
    with the other ones (`installation.public_origin`). A link is only worth
    sending if it resolves from wherever the notification is read, which is not
    this machine.
    """
    return installation.public_origin()


def chat_link(thread_id: str, context_key: str) -> str:
    """A deep link to the conversation that sent the notification, or empty.

    BOTH parts are required and neither is guessed. The route addresses a chat by
    its context key AND its thread id, and this module is handed them rather than
    looking them up: a link built from a guess at the key lands the operator in a
    different conversation than the one that had something to say, which is worse
    than sending no link because it looks like it worked. Empty whenever either
    part, or the installation's own address, is missing.
    """
    base = origin()
    key = (context_key or "").strip()
    if not base or not thread_id or not key:
        return ""
    return f"{base}/chat/{quote(key, safe='')}?thread={quote(thread_id, safe='')}"


def compose(text: str, *, link: str = "") -> str:
    """The delivered message: what happened, then how to get back to it.

    The link is the point of the second line. A notification that only says
    "the migration finished" leaves the operator to find the chat that said it;
    one that carries the address turns reading the notification into opening the
    work.
    """
    body = (text or "").strip()
    return f"{body}\n{link}".strip() if link else body


def send(text: str, *, link: str = "") -> dict:
    """Deliver one notification. Returns the transport's own answer.

    Never raises: the caller is a tool inside a turn, and an assistant needs to
    be told "this did not go" in a sentence it can pass on, not handed a
    traceback. But it never claims success it did not observe either -- `ok` is
    set only when the transport accepted the message and named it.
    """
    body = compose(text, link=link)
    if not body:
        return {"ok": False, "error": "there was nothing to send"}
    if not _token():
        return {"ok": False, "error": "this installation has no messaging credential configured"}

    target = installation.operator_dm_target()
    if not target:
        return {"ok": False, "error": "this installation has not declared how to reach the operator"}

    try:
        with httpx.Client(timeout=10.0) as client:
            channel_id = _dm_channel(client, target)
            r = client.post(
                f"{API}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {_token()}",
                         "Content-Type": "application/json"},
                json={"content": body[:1900]},
            )
            if r.status_code >= 300:
                return {"ok": False, "error": f"http {r.status_code}", "body": r.text[:200]}
            return {"ok": True, "message_id": (r.json() or {}).get("id")}
    except Exception as e:  # noqa: BLE001 - reported to the model, never raised at it
        log.warning("operator notification failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}
