#!/usr/bin/env python3
"""Run one turn of an app's in-app assistant chat, wait for it, summarize it.

    python3 scripts/app_chat_run.py <app-slug> "<message>" [--thread ID] [--new]
                                    [--timeout SECONDS] [--json]

It goes through the running backend's HTTP API exactly as the app's Chat tab
does: `POST /chat/threads` with the app's own scope (read from `GET /apps`,
which resolves it rather than spelling `app:<slug>`) and `POST
/chat/threads/{id}/turns`. The backend spawns the turn as its own child, so the
persona, MCP servers, environment, flags and model gateway are the ones the
operator's turns get, and the thread appears in that app's Chat tab.

Authentication is a session minted locally for the operator through
`auth_store.create_session`, the same path `scripts/smoke_chat.sh` and the
preview config use, and revoked when the script exits. Nothing is opened up.

Without `--thread` or `--new` it continues the app's most recently updated
chat. It refuses to post into a thread with a turn already running, because
that send would be injected into someone else's turn rather than start one.

Exit status: 0 the turn finished, 1 it was lost or the run failed, 2 timeout
(the turn is left running).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

import app_chat_summary as acs  # noqa: E402
from db import script_env  # noqa: E402

REPLY_CLIP = 800
POLL_S = 2.0


class Api:
    def __init__(self, base: str, session: str):
        self.base = base.rstrip("/")
        self.headers = {"Cookie": f"workshop_session={session}",
                        "x-workshop-client": "app-chat-run",
                        "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: dict | None = None,
             params: dict | None = None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}")


def app_scope(api: Api, slug: str) -> str:
    apps = api.call("GET", "/apps").get("apps") or []
    for app in apps:
        if app.get("slug") == slug and not app.get("archived_at"):
            return app.get("scope") or f"app:{slug}"
    raise SystemExit(f"no app with slug {slug!r}; known: "
                     + ", ".join(sorted(a.get("slug") or "?" for a in apps)))


def pick_thread(api: Api, scope: str, thread: str | None, new: bool) -> str:
    if thread:
        return thread
    if not new:
        rows = api.call("GET", "/chat/threads", params={"context": scope}) or []
        rows = [r for r in rows if r.get("context_key") == scope]
        if rows:
            return max(rows, key=lambda r: r.get("updated_at") or "")["id"]
    return api.call("POST", "/chat/threads", body={"context_key": scope})["id"]


def run_turn(api: Api, thread: str, message: str, timeout: float) -> dict:
    head = api.call("GET", f"/chat/threads/{thread}/events", params={"tail": 1})
    if head.get("active_turn"):
        raise SystemExit(f"thread {thread} already has a turn running; not injecting")
    cursor = int(head.get("last_seq") or 0)
    out = api.call("POST", f"/chat/threads/{thread}/turns",
                   body={"content": message, "client_id": str(uuid.uuid4())})
    turn_id = out["turn_id"]
    if out.get("queued") or out.get("injected"):
        raise SystemExit(f"send was {'queued' if out.get('queued') else 'injected'}, "
                         f"not started as a turn (turn {turn_id})")
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    state = "timeout"
    while time.monotonic() < deadline:
        page = api.call("GET", f"/chat/threads/{thread}/events", params={"after": cursor})
        for ev in page.get("events") or []:
            events.append(ev)
            if ev.get("kind") == "turn" and ev.get("turn_id") == turn_id \
                    and ev.get("state") in ("finished", "lost"):
                state = ev["state"]
        cursor = int(page.get("last_seq") or cursor)
        if state != "timeout":
            break
        time.sleep(POLL_S)
    return {"turn_id": turn_id, "state": state, "events": events}


def turn_window(run: dict) -> tuple[dict, str | None, str | None]:
    reply = next((e for e in run["events"] if e.get("role") == "assistant"
                  and (e.get("meta") or {}).get("turn_id") == run["turn_id"]), {})
    meta = reply.get("meta") or {}
    stamps = [e.get("ts") for e in run["events"]
              if e.get("kind") == "turn" and e.get("turn_id") == run["turn_id"]]
    start = meta.get("started_at") or (stamps[0] if stamps else None)
    end = meta.get("ended_at") or (stamps[-1] if len(stamps) > 1 else None)
    return reply, start, end


def build_report(api: Api, scope: str, thread: str, run: dict) -> dict:
    from services import scoped_exec
    reply, start, end = turn_window(run)
    meta = reply.get("meta") or {}
    t0, t1 = acs.parse_ts(start), acs.parse_ts(end)
    session = api.call("GET", f"/chat/threads/{thread}").get("claude_session")
    homes = list(dict.fromkeys([os.path.expanduser("~"), scoped_exec.turn_home()]))
    path = acs.find_transcript(session, homes) if session else None
    counts = acs.summarize(acs.in_window(acs.load_records(path), t0, t1)) if path else {}
    final = reply.get("content") or counts.get("last_text") or ""
    return {
        "scope": scope, "thread": thread, "turn_id": run["turn_id"],
        "state": run["state"],
        "duration_s": round((t1 - t0).total_seconds(), 1) if t0 and t1 else None,
        "flags": {k: meta[k] for k in ("stopped", "error", "timeout") if meta.get(k)},
        "session": session, "transcript": str(path) if path else None,
        **{k: v for k, v in counts.items() if k != "last_text"},
        "reply": final,
    }


def render(r: dict) -> str:
    lines = [f"thread   {r['thread']}  ({r['scope']})  turn {r['turn_id']}",
             f"status   {r['state']}  duration "
             + (f"{r['duration_s']}s" if r["duration_s"] is not None else "?")
             + (f"  {r['flags']}" if r["flags"] else "")]
    if r.get("requests") is not None:
        lines.append(f"model    {', '.join(r['models']) or '?'}  requests {r['requests']}"
                     f"  input {r['input_tokens']:,} ({r['input_tokens_cached']:,} cached)"
                     f"  output {r['output_tokens']:,}  stop {r['stop_reason']}")
        tools = ", ".join(f"{n} x{c}" for n, c in r["tool_calls"].items()) or "none"
        lines.append(f"tools    {tools}  ({len(r['tool_errors'])} errors)")
        lines += [f"  error  {e['tool']}: {e['error']}" for e in r["tool_errors"]]
        lines.append(f"compact  {len(r['compactions'])}" + "".join(
            f"  [{c['trigger']} {c['preTokens']}->{c['postTokens']}]" for c in r["compactions"]))
        lines.append(f"chars    thinking {r['thinking_chars']:,}  visible {r['text_chars']:,}")
    else:
        lines.append(f"transcript not found for session {r['session']}")
    reply = r["reply"].strip()
    clipped = reply[:REPLY_CLIP] + (" ..." if len(reply) > REPLY_CLIP else "")
    lines.append("reply    " + (clipped.replace("\n", "\n         ") or "(empty)"))
    lines.append(f"full     {r['transcript']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("message")
    ap.add_argument("--thread", help="post into this thread id")
    ap.add_argument("--new", action="store_true", help="start a new chat")
    ap.add_argument("--timeout", type=float, default=1800, help="seconds to wait")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()
    script_env.load_config_env()
    from services import auth_store
    owner = auth_store.operator_id()
    if owner is None:
        raise SystemExit("no operator account in auth.db")
    session = auth_store.create_session(owner, user_agent="app-chat-run")
    base = f"http://127.0.0.1:{os.environ.get('WORKSHOP_BACKEND_PORT', '8000')}"
    try:
        api = Api(base, session)
        scope = app_scope(api, args.slug)
        thread = pick_thread(api, scope, args.thread, args.new)
        report = build_report(api, scope, thread, run_turn(api, thread, args.message,
                                                           args.timeout))
    finally:
        auth_store.revoke_session(session)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return {"finished": 0, "timeout": 2}.get(report["state"], 1)


if __name__ == "__main__":
    sys.exit(main())
