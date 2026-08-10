#!/usr/bin/env python3
"""chat_event_bridge.py — the thin Discord<->event-bus poster (final-plan PM.3, Option A).

The ONLY job of this process is to move Discord messages on and off the workshop
event bus. There is NO `claude -p` loop and NO LLM in this process; that decouples
the model life-cycle from the network life-cycle, so a dispatcher restart never
drops the Discord connection.

  inbound   Discord gateway MESSAGE_CREATE  ->  POST /events/discord   (an envelope
            lands in .runtime/events.jsonl; the workshop pipeline decides + dispatches)
  outbound  a `discord-reply` event on the bus  ->  Discord REST send  (the chat bridge's
            dispatched turn writes the reply to the bus; this process delivers it)

Run under `~/.config/systemd/user/the chat bridge-event-bridge.service`. NOT enabled during
the migration safety window — the live surface is still the chat bridge-bot.service. See
that unit and agents/the chat bridge.md "Flipping this live".

Auth: subscription OAuth is irrelevant here (no model). The Discord bot token is
read from ~/.claude/channels/discord/.env (DISCORD_BOT_TOKEN). ANTHROPIC_API_KEY is
never read or set.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    import websockets
except ImportError:  # pragma: no cover - import guard, not a stub
    websockets = None

BUS_URL = os.environ.get("WORKSHOP_EVENT_BUS", "http://127.0.0.1:8000")
DISCORD_ENV = Path(os.environ.get(
    "DISCORD_CHANNEL_ENV", str(Path.home() / ".claude" / "channels" / "discord" / ".env")))
DISCORD_API = "https://discord.com/api/v10"
CURSOR_PATH = Path(os.environ.get(
    "WORKSHOP_DISCORD_REPLY_CURSOR",
    str(Path.home() / "workshop" / ".runtime" / "discord-reply-cursor.json")))
# Gateway intents: GUILD_MESSAGES (1<<9) + MESSAGE_CONTENT (1<<15) + DIRECT_MESSAGES (1<<12).
INTENTS = (1 << 9) | (1 << 12) | (1 << 15)
REPLY_SOURCE = "discord-reply"


def load_bot_token() -> str:
    """Read DISCORD_BOT_TOKEN from the Discord channel .env (never logged)."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token and DISCORD_ENV.is_file():
        for line in DISCORD_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISCORD_BOT_TOKEN=") and "=" in line:
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not token:
        raise RuntimeError(f"DISCORD_BOT_TOKEN not found in env or {DISCORD_ENV}")
    return token


def build_inbound_envelope(msg: dict) -> dict:
    """Map a Discord MESSAGE_CREATE payload to the workshop inbound envelope. Pure
    (no I/O), so it is unit-testable with a synthetic message and no network."""
    author = msg.get("author") or {}
    return {
        "source": "discord",
        "chat_id": str(msg.get("channel_id", "")),
        "message_id": str(msg.get("id", "")),
        "user": str(author.get("id", "")),
        "username": author.get("username", ""),
        "guild_id": str(msg.get("guild_id", "")) if msg.get("guild_id") else "",
        "ts": msg.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "content": msg.get("content", ""),
        "attachment_count": len(msg.get("attachments") or []),
    }


def post_inbound(envelope: dict, *, bus_url: str = BUS_URL, client: httpx.Client | None = None) -> int:
    """POST one inbound envelope to /events/discord. Returns the HTTP status. The
    bus does the logging + subscription fan-out; this call carries no model."""
    url = f"{bus_url.rstrip('/')}/events/discord"
    owns = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        r = client.post(url, json=envelope)
        return r.status_code
    finally:
        if owns:
            client.close()


def _load_cursor() -> str:
    try:
        return json.loads(CURSOR_PATH.read_text()).get("last_ts", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def _save_cursor(ts: str) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_PATH.write_text(json.dumps({"last_ts": ts}))


def send_discord_reply(reply: dict, *, token: str, client: httpx.Client | None = None) -> int:
    """Deliver one outbound reply to Discord REST. `reply` carries `chat_id`,
    `content`, and optional `reply_to` (a message_id to quote). Returns HTTP status."""
    chat_id = str(reply.get("chat_id", ""))
    content = reply.get("content", "")
    if not chat_id or not content:
        return 0
    body: dict = {"content": content}
    if reply.get("reply_to"):
        body["message_reference"] = {"message_id": str(reply["reply_to"])}
    owns = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        r = client.post(
            f"{DISCORD_API}/channels/{chat_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json=body,
        )
        return r.status_code
    finally:
        if owns:
            client.close()


def drain_replies(*, token: str, bus_url: str = BUS_URL, client: httpx.Client | None = None) -> int:
    """Read new `discord-reply` events off the bus and deliver each to Discord.
    Dedup by an ISO-ts cursor so a reply is sent once. Returns count delivered."""
    owns = client is None
    client = client or httpx.Client(timeout=15.0)
    delivered = 0
    try:
        r = client.get(f"{bus_url.rstrip('/')}/events/recent",
                       params={"source": REPLY_SOURCE, "limit": 50})
        if r.status_code != 200:
            return 0
        events = list(reversed(r.json().get("events") or []))  # oldest first
        cursor = _load_cursor()
        newest = cursor
        for ev in events:
            ts = ev.get("ts", "")
            if cursor and ts <= cursor:
                continue
            if send_discord_reply(ev.get("body") or {}, token=token, client=client) in (200, 201):
                delivered += 1
            if ts > newest:
                newest = ts
        if newest and newest != cursor:
            _save_cursor(newest)
        return delivered
    finally:
        if owns:
            client.close()


async def _reply_pump(token: str, stop: asyncio.Event) -> None:
    """Poll the bus for outbound replies every 2s until stopped."""
    with httpx.Client(timeout=15.0) as client:
        while not stop.is_set():
            try:
                drain_replies(token=token, client=client)
            except Exception as e:  # keep the pump alive across transient bus errors
                print(f"[bridge] reply drain error: {e}", file=sys.stderr, flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass


async def _gateway_loop(token: str, stop: asyncio.Event) -> None:
    """Connect to the Discord gateway, identify, heartbeat, and forward each
    inbound MESSAGE_CREATE (that is not our own bot's message) to the event bus."""
    if websockets is None:
        raise RuntimeError("the `websockets` package is required for the gateway loop")
    gw = "wss://gateway.discord.gg/?v=10&encoding=json"
    bus = httpx.Client(timeout=10.0)
    self_id = ""
    try:
        async with websockets.connect(gw, max_size=None) as ws:
            hello = json.loads(await ws.recv())
            heartbeat_ms = hello["d"]["heartbeat_interval"]

            async def heartbeat():
                while not stop.is_set():
                    await asyncio.sleep(heartbeat_ms / 1000.0)
                    await ws.send(json.dumps({"op": 1, "d": None}))

            hb = asyncio.create_task(heartbeat())
            await ws.send(json.dumps({
                "op": 2,
                "d": {
                    "token": token,
                    "intents": INTENTS,
                    "properties": {"os": "linux", "browser": "chat-bridge", "device": "chat-bridge"},
                },
            }))
            try:
                while not stop.is_set():
                    frame = json.loads(await ws.recv())
                    t = frame.get("t")
                    if t == "READY":
                        self_id = str(((frame.get("d") or {}).get("user") or {}).get("id", ""))
                    elif t == "MESSAGE_CREATE":
                        msg = frame.get("d") or {}
                        author = msg.get("author") or {}
                        if author.get("bot") or str(author.get("id", "")) == self_id:
                            continue  # never echo our own / other bots' messages
                        env = build_inbound_envelope(msg)
                        try:
                            post_inbound(env, client=bus)
                        except Exception as e:
                            print(f"[bridge] post_inbound error: {e}", file=sys.stderr, flush=True)
            finally:
                hb.cancel()
    finally:
        bus.close()


async def _run() -> None:
    token = load_bot_token()
    stop = asyncio.Event()
    await asyncio.gather(_gateway_loop(token, stop), _reply_pump(token, stop))


def main() -> int:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
