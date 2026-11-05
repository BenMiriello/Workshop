"""A turn whose model requests keep failing ends, saying so, instead of retrying.

The CLI retries a failed model request by itself and reports it only in its
stream, as a `system` event with subtype `api_retry` (attempt, status, error).
Nothing reaches the chat, which shows the turn working with nothing under it.
Against a cloud API that is the right default: an overload clears. Against a
model server on the operator's own hardware it is not: a model that has been
unloaded, or a server that is down, fails every request the same way, and a
turn left to retry it held a working indicator for two hours on 2026-10-03
until Stop ended it.

So a turn on a provider that drives the CLI from outside
(`llm_providers.redirects_cli`) keeps a `Streak` of those events, cleared only
by a request that FINISHED, and is ended with `verdict`'s message once the
streak passes either bound:

- `MAX_IN_A_ROW` failed requests with none finishing between them (more for a
  turn whose CLI was deliberately given more retries, `limit_for`);
- `FAIL_CAP_S` since the streak's first failure while nothing is coming, which
  catches a server that fails slowly (each request waiting out its own timeout).

Output alone does not clear it. A model server that reloads cuts the answer it
is streaming, and a request that streams a little and is cut every time used to
reset the count on each try and never end the turn (2026-10-22). The CLI says
which responses it gave up on: it closes a cut one itself with a `message_stop`
carrying `abandoned_blocks`, so that is a failure, and a `message_stop` without
it, or a whole non-streamed message, is a finished request (`observe`).

The CLI's own retry count for those turns is lowered beside this, in the
provider's `cli_env`, so usually the CLI gives up first; this is the bound that
holds whatever the CLI does.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

MAX_IN_A_ROW = int(os.environ.get("WORKSHOP_TURN_MODEL_FAILURES", "5"))
FAIL_CAP_S = float(os.environ.get("WORKSHOP_TURN_MODEL_FAIL_CAP_S", "600"))


def limit_for(cli_retries: object) -> int:
    """The streak that ends a turn whose CLI retries each request
    `cli_retries` times: never fewer than `MAX_IN_A_ROW`, and never short of
    the CLI's own budget, so a turn given retries to wait out a restart of its
    app's gateway (`app_chat_gateway.GATEWAY_RETRIES`) is not ended halfway
    through them. Past that budget the CLI gives the request up itself."""
    try:
        retries = int(cli_retries or 0)
    except (TypeError, ValueError):
        retries = 0
    return max(MAX_IN_A_ROW, retries + 1)


def describe(event: dict) -> str:
    """One failed request in a few words: its status and the error text."""
    status = event.get("error_status")
    error = str(event.get("error") or "").strip()
    if event.get("no_response"):
        error = error or "no response"
    words = " ".join(p for p in (str(status) if status else "", error) if p)
    return words[:200] or "no detail"


@dataclass
class Streak:
    """Failed model requests since one last finished.

    `limit` is how many in a row end the turn: `MAX_IN_A_ROW`, or more for a
    turn whose CLI was given more retries on purpose (`limit_for`). `live` is
    whether the request now running has produced anything, which holds off the
    time bound while it streams. `cut` marks a cut response counted on the
    CLI's own `message_stop`, so the `api_retry` that reports the same request
    next is not counted twice."""
    count: int = 0
    since: float | None = None
    last: str = ""
    limit: int = MAX_IN_A_ROW
    live: bool = False
    cut: bool = False

    def observe(self, kind: str, payload: dict, now: float) -> None:
        """Fold one parsed stream event (`build_streaming._parse_stream_event`)."""
        if kind in ("_multi", "text", "tool", "thinking"):
            self.live, self.cut = True, False
            if kind == "_multi" and payload.get("stop_reason"):
                self.progressed()
        elif kind == "meta" and payload.get("subtype") == "api_retry":
            self.failed(payload, now)
        elif kind == "meta" and payload.get("event") == "message_stop":
            if payload.get("abandoned"):
                self.cut_short(now)
            else:
                self.progressed()

    def failed(self, event: dict, now: float) -> None:
        if self.cut:
            self.cut = False
            return
        self._count(describe(event), now)

    def cut_short(self, now: float) -> None:
        self._count("the answer was cut off after it had started"
                    if self.live else "the answer was cut off", now)
        self.cut = True

    def _count(self, what: str, now: float) -> None:
        self.count += 1
        if self.since is None:
            self.since = now
        self.last = what
        self.live = False

    def progressed(self) -> None:
        self.count, self.since, self.last = 0, None, ""
        self.live = self.cut = False

    def verdict(self, now: float) -> str | None:
        """The message to end the turn with, or None while it may go on."""
        if not self.count:
            return None
        if self.count >= self.limit:
            how = f"{self.count} requests in a row failed"
        elif (not self.live and self.since is not None
              and now - self.since >= FAIL_CAP_S):
            minutes = max(1, round((now - self.since) / 60))
            how = f"no request succeeded in {minutes} min"
        else:
            return None
        return (f"Stopped: the model kept failing ({how}; last: {self.last}). "
                "Its server may have unloaded the model or be restarting. "
                "Send the message again once it is back.")
