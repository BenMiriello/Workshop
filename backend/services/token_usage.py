"""One place that turns a vendor usage block into named quantities.

WHY THIS MODULE EXISTS. Four different questions were being answered with one
number, by three writers, in three units, into one ledger column called `tokens`.
They cannot share a number:

  (a) OCCUPANCY  how full is this context window right now.
      input + cache_read + cache_creation, parent conversation only.
      A max/latest over calls, NEVER a sum, and it drops after a compaction.
  (b) SPEND      what did this turn cost to serve, by volume.
      All four components at parity, summed over distinct CALLS.
  (c) EFFECTIVE  what did this draw down of the thing that actually runs out.
      Rate-limit shaped, so cache reads count far below parity. This is the
      admission and budget quantity.
  (d) MONEY      what it would cost at metered prices. Needs the model id and a
      price table, so it is NOT here; it lands with the price table (16625867).

(a) and (b) differ because one is a max and the other is a sum. (b) and (c)
differ because they weight cache reads differently, by a measured factor of
about 7x on our own turns. (c) and (d) differ because (c) is model-independent
and (d) is not, so a budget in tokens silently changes meaning when the model
does.

DEDUPLICATION IS THE OTHER HALF. The CLI emits one journal frame per content
block, and every frame of one assistant message carries the SAME usage object.
Adding frames instead of calls is not a rounding error. Measured 2026-08-26 over
four real session journals in this repo:

    frames  calls   frame_sum      dedup_sum     inflation
       267    118   50,371,949     22,689,203      2.22x
       138     60   17,697,608      7,503,105      2.36x
        55     19    4,433,928      1,537,305      2.88x
       176     80   24,589,642     11,887,153      2.07x

(counting-tokens-2026-08-25.md reported 1.54x and 1.59x from two turns; on
current journals it is worse than that, not better.) One assistant message was
observed carrying the identical usage object across exactly three frames.

Pure stdlib and no `services.*` imports on purpose, so this is importable from
the FastAPI app, from voice_worker, and from the shell through the usage_ledger
CLI shim. It holds no state of its own and does no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CACHE_READ_WEIGHT", "Usage", "parse", "occupancy", "spend", "effective",
    "TurnMeter",
]

# A subscription's rate limit counts a cache read far below a fresh input token,
# so metering cache reads at parity trips a budget long before the real quota is
# spent. 0.1 is the weight scripts/ralph.sh has used since review L3; it lives
# here now so it stops being restated in shell. It is a WEIGHT, not a price: the
# money answer needs a per-model price table and is a separate concern.
CACHE_READ_WEIGHT = 0.1


@dataclass(frozen=True)
class Usage:
    """The four components of one API call, as the vendor reported them.

    Kept as components rather than a total because a total cannot be revisited.
    Storing only a sum is why the 6.5 billion tokens of existing history cannot
    be re-evaluated under a different weight or a price table.
    """

    input: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input=self.input + other.input,
            cache_read=self.cache_read + other.cache_read,
            cache_creation=self.cache_creation + other.cache_creation,
            output=self.output + other.output,
        )

    def as_dict(self) -> dict[str, int]:
        """The shape a ledger row stores under `components`."""
        return {"input": self.input, "cache_read": self.cache_read,
                "cache_creation": self.cache_creation, "output": self.output}


def _int(v: object) -> int:
    """A vendor field to a non-negative int, tolerating None, strings and junk.

    Tolerant on purpose: a usage block is untrusted input from another process,
    and a turn must never fail because a field arrived as a string. A negative
    value is clamped rather than trusted, since every one of these is a count.
    """
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def parse(u: object) -> Usage:
    """One vendor usage block to components.

    THE ONLY PLACE A VENDOR FIELD NAME APPEARS. Anything that is not a dict, or
    is missing fields, yields zeros rather than raising: metering must never be
    the thing that costs a turn.
    """
    if not isinstance(u, dict):
        return Usage()
    return Usage(
        input=_int(u.get("input_tokens")),
        cache_read=_int(u.get("cache_read_input_tokens")),
        cache_creation=_int(u.get("cache_creation_input_tokens")),
        output=_int(u.get("output_tokens")),
    )


def occupancy(u: Usage) -> int:
    """(a) How full the window is: everything the model READ.

    Output is excluded on purpose, because it is not context. This is a per-call
    reading that is overwritten, never accumulated: summing it across a
    conversation produces a number larger than any window and means nothing.
    """
    return u.input + u.cache_read + u.cache_creation


def spend(u: Usage) -> int:
    """(b) Volume billed: all four components at parity, summed over CALLS."""
    return u.input + u.cache_read + u.cache_creation + u.output


def effective(u: Usage) -> int:
    """(c) The rate-limit-shaped draw-down, with cache reads at CACHE_READ_WEIGHT.

    Reproduces exactly what scripts/ralph.sh has computed inline per iteration,
    including its truncation: the weighted term is floored on its own before the
    sum, rather than the sum being floored at the end. That ordering is kept
    deliberately so this module and the worker cannot disagree by a token, which
    is the whole point of moving the arithmetic here.

    APPLY PER CALL AND THEN SUM, never sum components and weight once: the two
    differ by the truncation, and per-call is what the existing worker numbers
    were built from. `TurnMeter.effective` does it the same way.
    """
    return (u.input
            + int(CACHE_READ_WEIGHT * u.cache_read)
            + u.cache_creation
            + u.output)


class TurnMeter:
    """Accumulates one turn's usage across stream frames, deduplicated by API
    message id, keeping the main conversation separate from its delegates.

    TWO RULES, and they pull in opposite directions, which is why one object owns
    both rather than each caller reimplementing them:

      - DEDUPLICATE BY MESSAGE ID. Several frames carry one call's usage. The
        first frame for an id wins; later frames for the same id are ignored
        rather than added. See the module docstring for the measured cost of
        getting this wrong.
      - A DELEGATE'S TOKENS ARE REALLY SPENT, BUT ITS WINDOW IS NOT OURS. So
        spend and effective count everything including delegates, while
        occupancy reads the main conversation only: a subagent has its own
        context window, and letting it set the meter would show the operator a
        number that is not about the conversation in front of them.
    """

    def __init__(self) -> None:
        self._calls: dict[str, Usage] = {}
        self._order: list[str] = []
        self._frames = 0
        self._anonymous: list[Usage] = []
        self._last_main: Usage | None = None
        self._result_usage: Usage | None = None
        self._result_cost_usd: float | None = None

    def observe(self, message_id: str | None, usage: object, *,
                parent: str | None = None) -> None:
        """Record one stream frame.

        `parent` names the conversation this call belongs to: None is the main
        conversation, and any other value marks a delegate with its own window.

        A frame with NO message id cannot be deduplicated, so it is kept
        separately and always counted. That is the safer direction: an
        undercount is invisible, whereas the overcount this class exists to stop
        is the one we can bound.
        """
        u = parse(usage)
        self._frames += 1
        first_time = True
        if message_id:
            if message_id in self._calls:
                first_time = False
            else:
                self._calls[message_id] = u
                self._order.append(message_id)
        else:
            self._anonymous.append(u)
        if first_time and parent is None:
            self._last_main = u

    def observe_result(self, usage: object, cost_usd: float | None = None) -> None:
        """Record the CLI's own end-of-turn `result` envelope.

        Kept ALONGSIDE the frame sum rather than replacing it, and deliberately
        not folded in with an `or`. On the one turn where both were present the
        result reported 3,526,423 against a frame sum of 10,721,985, and the
        reason for that gap is unverified. Recording both lets a row say which
        number it used instead of hiding the choice.
        """
        self._result_usage = parse(usage)
        if cost_usd is not None:
            try:
                self._result_cost_usd = float(cost_usd)
            except (TypeError, ValueError):
                self._result_cost_usd = None

    @property
    def calls(self) -> int:
        """Distinct API calls seen. The direct check on frame inflation, and a
        better cost predictor than a whole-task percentile."""
        return len(self._calls) + len(self._anonymous)

    @property
    def frames(self) -> int:
        """Frames observed, including the duplicates that were discarded."""
        return self._frames

    @property
    def totals(self) -> Usage:
        """Deduplicated component sum over every call, delegates included."""
        out = Usage()
        for mid in self._order:
            out = out + self._calls[mid]
        for u in self._anonymous:
            out = out + u
        return out

    @property
    def occupancy(self) -> int:
        """(a) The main conversation's most recent reading, or 0 if none.

        THE LATEST CALL, NOT THE MAX. Ticket 189b9e0e's acceptance says "a max
        over parent-only calls", and for a turn whose context only grows the two
        are the same number. They diverge in exactly the case the quantity
        exists to handle: a compaction shrinks the window, and a max would go on
        reporting the pre-compaction peak forever, which is the stale reading
        that counting the stored transcript already gave us and that measuring
        the usage block was supposed to fix.
        """
        return 0 if self._last_main is None else occupancy(self._last_main)

    @property
    def spend(self) -> int:
        """(b) Volume billed across every distinct call, delegates included."""
        return spend(self.totals)

    @property
    def effective(self) -> int:
        """(c) Rate-limit-shaped draw-down, weighted PER CALL and then summed,
        which is how the worker has always computed it."""
        total = 0
        for mid in self._order:
            total += effective(self._calls[mid])
        for u in self._anonymous:
            total += effective(u)
        return total

    @property
    def result_totals(self) -> Usage | None:
        """The `result` envelope's own components, if the turn reported one."""
        return self._result_usage

    @property
    def result_cost_usd(self) -> float | None:
        """The vendor's own dollar figure. Free, and currently discarded."""
        return self._result_cost_usd
