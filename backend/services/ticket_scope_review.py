"""Is this ticket the right SIZE and SHAPE to hand to a worker?

`ticket_readiness` answers a different, cheaper question: does the ticket state
its objective, its acceptance, where to start and what not to touch. It is a
regex pass and it cannot tell a well-written ticket that happens to be four
tickets in a trench coat from a well-written ticket.

This is the judgement pass. It reads the ticket and says one of:

    ok             one deliverable, go
    split          several deliverables; here they are, as sub-tickets
    underspecified something load-bearing is missing or unknowable from the text

plus the blockers it can see and, when the work is genuinely sequential, the
stages.

THIS MODULE IS THE ENGINE, NOT A RUNNER (autonomy-implementation 2026-08-13 §5.1).
It decides and returns; it writes nothing, reads no store and knows no scope.
Two callers drive it:

  * on demand against a DRAFT, before the operator approves, which is the moment
    the answer is worth most because after approval a worker spends money on it;
  * `triager.run_queue_triage`, once per `todo` ticket whose spec has changed
    since it was last judged.

IT ONLY PROPOSES. Applying a split creates child drafts, and those need approving
like any other proposal. The review never queues work and never edits the ticket's
own text; a review that silently rewrote the ticket would be the system deciding
scope, which is the thing the operator asked to be able to trust.

Sub-ticket depth is capped at two levels (entity-model 2026-07-18), so a ticket
that already has a parent cannot be split again.

TWO VOCABULARIES, AND WHY THEY DIFFER BY EXACTLY ONE WORD
---------------------------------------------------------
`VERDICTS` here is three long; `tasks.TRIAGE_DECISIONS` (the mark the queue
triager stores) is four, adding `"none"`. That is deliberate and the extra word
is NOT an engine verdict:

    ok | split | underspecified   what the REVIEW concluded about the ticket
    none                          what the TRIAGER did, when the review's
                                  conclusion was not actionable

A model is never asked for `none` and `_parse` will never return it, so the
prompt keeps three options and the closed set here stays three. `none` is the
runner's own outcome for "I looked at this and there was nothing to do": the
review said `underspecified` but named no blocker anyone could answer, or the
ticket is already waiting on an unanswered question and a second one would
clobber it. Widening `VERDICTS` to four would put a word in the prompt that the
model must never choose, and a fourth option a model can see is a fourth option
a model will eventually pick.
"""
from __future__ import annotations

import json
import logging
import re

from services import llm_router, ticket_format

log = logging.getLogger(__name__)

VERDICTS = ("ok", "split", "underspecified")
MAX_SUBTICKETS = 6

# An em-dash and whatever space it is sitting in. Matching, not writing one.
_EM_DASH = re.compile(r"\s*—\s*")

PROMPT = """You are reviewing ONE work ticket before a human approves it and an autonomous
worker is asked to do it. The worker will see the ticket text and nothing else:
not this review, not the conversation the ticket came from.

Answer three questions.

1. IS IT ONE PIECE OF WORK? A ticket should have one deliverable that can be
   finished and judged on its own. If it names several independent deliverables,
   it must be split. Do NOT split work that is genuinely one deliverable just
   because it touches several files, and do NOT split along the lines of "write
   the code" / "write the test" / "update the docs". Those are steps in one
   ticket, not separate tickets.

2. WHAT BLOCKS IT? Anything the ticket depends on that is not established: a
   decision nobody has made, a credential that does not exist, another piece of
   work that must land first, an unknown that would force the worker to guess.
   Say only what you can support from the ticket text.

3. IS IT SEQUENTIAL? If the work only makes sense in a fixed order, list the
   stages. If order does not matter, return an empty list. Most tickets are not
   sequential; an empty list is the common answer.

Return STRICT JSON, no prose, no code fence:

{
  "verdict": "ok" | "split" | "underspecified",
  "reason": "one sentence, addressed to the operator",
  "subtickets": [
    {"title": "short imperative line, under 80 characters",
     "objective": "the concrete outcome, one sentence",
     "output": "what done looks like",
     "entry_points": "files, commands or symbols to start from",
     "boundaries": "what NOT to touch"}
  ],
  "blockers": ["one line each"],
  "stages": ["one line each, in order"]
}

`subtickets` is empty unless the verdict is "split". When you do split, every
sub-ticket must stand completely on its own: no "as above", no "the rest of it",
no reference to its siblings. Between them they must cover the whole of the
original ticket and add nothing that was not in it.

Do not invent a file path, a command or an acceptance test that the ticket does
not support. "The ticket does not say" is a useful blocker; a confident guess is
a wasted run.

=== TICKET ===
{ticket}
=== END TICKET ===

JSON:"""


def _ticket_text(task: dict) -> str:
    title = str(task.get("title") or "").strip()
    body = str(task.get("body") or "").strip()
    lines = [f"Title: {title}"]
    if body:
        lines.append(body)
    effort = task.get("effort")
    if effort:
        lines.append(f"(the operator's size estimate: {effort})")
    touches = task.get("touches") or []
    if touches:
        lines.append(f"(declared file lane: {', '.join(touches)})")
    return "\n\n".join(lines)


def _clean(value, limit: int = 400) -> str:
    # Every string this module returns is read by the operator: a reason on a
    # ticket, a question in the inbox, the body of a child draft. Em-dashes are
    # stripped the way `ticket_drafts` and the chat title path strip them, which
    # is enforcement rather than prose. A first live pass over the development
    # queue put one in 6 of 19 reasons, so asking the prompt nicely is not
    # enough. The surrounding spaces go with it: a bare `.replace` leaves
    # "attached , the worker", which is the same defect one character over.
    return " ".join(_EM_DASH.sub(", ", str(value or "")).split())[:limit]


def parse_failure(raw: str) -> str:
    """Why `_parse` gave up, in a sentence the pass can record and a person can act on.

    `_parse` returns an empty dict from four different places and the caller used
    to collapse all four into "the scope review did not return a usable answer".
    That sentence cannot be acted on: it does not say whether the model returned
    nothing, returned prose, or returned JSON that would not decode, and those
    have three different fixes. It hid a live defect for the whole time the queue
    pass existed -- the pass ran, recorded errors, left the tickets unmarked, and
    reported `decided 3` as though the other four had simply not been due.

    The raw reply head goes in the message because it is the one thing that
    identifies which of the three happened at a glance.
    """
    text = (raw or "").strip()
    if not text:
        return "the scope review returned no text at all"
    head = " ".join(text.split())[:200]
    if "{" not in text:
        return f"the scope review answered in prose, not JSON: {head}"
    return f"the scope review returned JSON that would not decode: {head}"


def _parse(raw: str) -> dict:
    """Pull the review out of a model reply. Tolerant of a fence or stray prose
    around the JSON, which is a formatting slip and not a reason to lose the
    answer and make the operator wait again."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace < 0:
            return {}
        text = text[brace:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        verdict = "ok"

    subs: list[dict] = []
    for s in (data.get("subtickets") or [])[:MAX_SUBTICKETS]:
        if not isinstance(s, dict):
            continue
        title = _clean(s.get("title"), 140)
        if not title:
            continue
        subs.append({
            "title": title,
            "body": ticket_format.compose(
                objective=_clean(s.get("objective")),
                output=_clean(s.get("output")),
                entry_points=_clean(s.get("entry_points")),
                boundaries=_clean(s.get("boundaries")),
            ),
        })
    # A "split" with nothing to split into is not a split. Trust the list, not
    # the label: a model that says split and then names one piece of work has
    # contradicted itself, and the label is the cheaper half to discard.
    if verdict == "split" and len(subs) < 2:
        verdict = "ok" if not subs else "underspecified"
        subs = []

    return {
        "verdict": verdict,
        "reason": _clean(data.get("reason"), 500),
        "subtickets": subs,
        "blockers": [_clean(b) for b in (data.get("blockers") or [])[:8] if _clean(b)],
        "stages": [_clean(s) for s in (data.get("stages") or [])[:10] if _clean(s)],
    }


async def review(task: dict) -> dict:
    """Judge one ticket's scope. Never writes anything."""
    import asyncio

    prompt = PROMPT.replace("{ticket}", _ticket_text(task))
    # SINGLE-SHOT TEXT, NO TOOLS, and this is the fix for a measured failure
    # rather than a precaution. The call used to be a bare `call_cloud(prompt,
    # model=...)`, which is `llm_router`'s third mode: agentic, full tool and MCP
    # inheritance, no turn cap. Measured 2026-09-01 on the real development
    # queue, that mode answered in prose about what it had just done instead of
    # in JSON, so `_parse` returned nothing and the pass recorded an error:
    # 4 of the first 7 tickets, and 3 of 4 on a second scope. A direct probe on
    # two tickets, both modes, same prompt: agentic took 85s and 115s and parsed
    # 0 of 2; this call took 4.5s and 7.0s and parsed 2 of 2. The agentic reply
    # also described making tool calls, in a module whose own docstring says it
    # writes nothing and reads no store -- so the flags below are what make that
    # sentence true, not the sentence.
    #
    # `max_turns=1` returns exit 1 with `stop_reason=max_turns` once the model
    # has spoken; `call_cloud` recovers the text and that is the answer. The note
    # pass one module over has passed exactly these three flags since it was
    # written (`triager._decide_llm`), which is why it is the pass that works.
    res = await asyncio.to_thread(
        llm_router.call_cloud, prompt, model=llm_router.TIER_SONNET_5,
        tools_disabled=True, max_turns=1, timeout_s=180)
    raw = res.get("text") or ""
    out = _parse(raw)
    if not out:
        raise ValueError(parse_failure(raw))
    # Depth is capped at two levels, so a ticket that is already a sub-ticket
    # cannot be split again. Said here rather than swallowed, so the operator
    # learns why the button is not offered.
    if task.get("parent") and out["verdict"] == "split":
        out["verdict"] = "underspecified"
        out["reason"] = (
            "This is already a sub-ticket and sub-tickets cannot be split again "
            "(depth is capped at two). " + out["reason"]
        )
        out["subtickets"] = []
    return out
