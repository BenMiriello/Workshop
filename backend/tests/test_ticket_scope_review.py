"""Is the ticket the right SIZE, and what happens when it is not.

`ticket_readiness` is a regex pass: it cannot tell a well-written ticket from a
well-written ticket that is really four tickets. This is the judgement pass, and
it only ever PROPOSES. It writes nothing at all: applying a split is the caller's
job (today `triager.run_queue_triage`), and the review never edits the ticket's
own text. A review that silently rewrote scope would be the system deciding
scope, which is the thing the operator asked to be able to trust.

RESTORED, DELIBERATELY SMALLER THAN IT WAS. The original half of this file drove
`POST /api/tasks/{scope}/{id}/split` through a TestClient. That endpoint no
longer exists (it went with the ticket-surface rewrite in 79e00c5, and the
module's last import went with it), so those cases would test a 404. Applying a
split is now covered where it actually happens, in `test_queue_triage.py`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks, ticket_scope_review as sr  # noqa: E402

OK_REPLY = ('{"verdict":"ok","reason":"One deliverable.",'
            '"subtickets":[],"blockers":[],"stages":[]}')

SPLIT_REPLY = (
    '{"verdict":"split","reason":"This names three independent deliverables.",'
    '"subtickets":['
    '{"title":"Add the binding record","objective":"Store one binding per scope.",'
    '"output":"A binding round-trips.","entry_points":"backend/services/tracker_bindings.py",'
    '"boundaries":"No UI."},'
    '{"title":"Add the settings form","objective":"Bind a scope from Settings.",'
    '"output":"The form creates a binding.","entry_points":"frontend/src/pages/settings",'
    '"boundaries":"No sync engine."}],'
    '"blockers":["Nobody has registered the GitHub App yet."],'
    '"stages":[]}'
)


def _stub(monkeypatch, raw: str):
    from services import llm_router
    monkeypatch.setattr(llm_router, "call_cloud", lambda *a, **k: {"text": raw})


# ---- parsing the verdict ----------------------------------------------------


def test_a_split_is_parsed_with_composed_bodies(monkeypatch):
    _stub(monkeypatch, SPLIT_REPLY)
    out = asyncio.run(sr.review({"title": "Do the tracker work", "body": "All of it."}))
    assert out["verdict"] == "split"
    assert len(out["subtickets"]) == 2
    # Sub-ticket bodies go through the shared formatter, so a child is born ready.
    assert out["subtickets"][0]["body"].startswith("Objective: Store one binding per scope.")
    assert out["blockers"] == ["Nobody has registered the GitHub App yet."]


def test_a_split_with_one_piece_is_not_a_split(monkeypatch):
    """A model that says "split" and then names one deliverable has contradicted
    itself. The list is the evidence; the label is the cheaper half to discard."""
    _stub(monkeypatch, '{"verdict":"split","reason":"hm","subtickets":'
                       '[{"title":"Just the one","objective":"x","output":"y"}]}')
    out = asyncio.run(sr.review({"title": "A ticket", "body": ""}))
    assert out["verdict"] == "underspecified"
    assert out["subtickets"] == []


def test_a_split_with_no_pieces_is_ok(monkeypatch):
    _stub(monkeypatch, '{"verdict":"split","reason":"hm","subtickets":[]}')
    out = asyncio.run(sr.review({"title": "A ticket", "body": ""}))
    assert out["verdict"] == "ok"


def test_an_unknown_verdict_falls_back_to_ok(monkeypatch):
    _stub(monkeypatch, '{"verdict":"banana","reason":"?"}')
    assert asyncio.run(sr.review({"title": "A ticket"}))["verdict"] == "ok"


def test_a_fenced_reply_is_still_read(monkeypatch):
    _stub(monkeypatch, "Sure!\n```json\n" + SPLIT_REPLY + "\n```")
    assert asyncio.run(sr.review({"title": "A ticket"}))["verdict"] == "split"


def test_an_unusable_reply_raises(monkeypatch):
    _stub(monkeypatch, "I could not do that.")
    with pytest.raises(ValueError):
        asyncio.run(sr.review({"title": "A ticket"}))


def test_a_subticket_cannot_be_split_again(monkeypatch):
    """Depth is capped at two levels, and the reason is stated rather than
    swallowed, so the operator learns why the split is not offered."""
    _stub(monkeypatch, SPLIT_REPLY)
    out = asyncio.run(sr.review({"title": "A child", "parent": "abc"}))
    assert out["verdict"] == "underspecified"
    assert out["subtickets"] == []
    assert "depth is capped" in out["reason"]


# ---- the engine writes nothing, and never says `none` -----------------------


def test_the_review_never_returns_the_marks_fourth_word(monkeypatch):
    """`tasks.TRIAGE_DECISIONS` has four values and `VERDICTS` has three. `none`
    is the RUNNER's outcome, never the engine's, so a model that tries to pick it
    gets the same treatment as any other unknown verdict."""
    assert set(sr.VERDICTS) < set(tasks.TRIAGE_DECISIONS)
    assert "none" not in sr.VERDICTS
    assert "none" not in sr.PROMPT
    _stub(monkeypatch, '{"verdict":"none","reason":"nothing to do"}')
    assert asyncio.run(sr.review({"title": "A ticket"}))["verdict"] == "ok"


def test_em_dashes_never_reach_the_operator(monkeypatch):
    """Everything this returns is read by a person: a reason on a ticket, a
    question in the inbox, a child draft's body. A live pass over the
    development queue put an em-dash in 6 of 19 reasons."""
    _stub(monkeypatch,
          '{"verdict":"underspecified","reason":"It needs root \\u2014 nobody said so.",'
          '"blockers":["The interface \\u2014 wired or wifi \\u2014 is not named."]}')
    out = asyncio.run(sr.review({"title": "A ticket"}))
    # And the space it was sitting in goes with it: a bare replace leaves
    # "root ,  nobody", which is the same defect one character over.
    assert out["reason"] == "It needs root, nobody said so."
    assert out["blockers"] == ["The interface, wired or wifi, is not named."]


def test_the_review_writes_nothing(monkeypatch):
    """It takes a dict and returns a dict. There is no store in its signature and
    no store in its imports, which is what makes it safe to call from a pass that
    is itself capped."""
    import inspect

    _stub(monkeypatch, SPLIT_REPLY)
    assert "root" not in inspect.signature(sr.review).parameters
    src = Path(sr.__file__).read_text()
    for forbidden in ("tasks.add", "tasks.update", "write_text", "open("):
        assert forbidden not in src, forbidden


# ---- the model call is single-shot text, and a failure says which failure ----
#
# Both of these pin a measured defect rather than a hypothetical. `review` used
# to call `llm_router.call_cloud` with no flags at all, which is its AGENTIC
# mode: full tool and MCP inheritance, no turn cap. On the real development
# queue on 2026-09-01 that mode answered in prose about the tool calls it had
# just made, `_parse` returned nothing, and the pass recorded 4 errors in its
# first 7 tickets. A direct probe over the same two tickets in both modes:
# agentic 85s and 115s, 0 of 2 parsed; single-shot 4.5s and 7.0s, 2 of 2 parsed.
# The prose reply also described making tool calls, in a module whose docstring
# says it writes nothing.


def test_the_review_asks_for_text_only_and_caps_the_turn(monkeypatch):
    seen: dict = {}

    def _spy(prompt, **kw):
        seen.update(kw)
        return {"text": OK_REPLY}

    from services import llm_router
    monkeypatch.setattr(llm_router, "call_cloud", _spy)
    asyncio.run(sr.review({"title": "A ticket"}))
    assert seen.get("tools_disabled") is True
    assert seen.get("max_turns") == 1
    assert seen.get("timeout_s")


def test_a_failure_names_its_stage_and_shows_what_came_back(monkeypatch):
    # Three different failures, three different sentences. They used to be one.
    _stub(monkeypatch, "")
    with pytest.raises(ValueError, match="no text at all"):
        asyncio.run(sr.review({"title": "A ticket"}))

    _stub(monkeypatch, "This turn made no file changes, I only ran grep.")
    with pytest.raises(ValueError, match="answered in prose"):
        asyncio.run(sr.review({"title": "A ticket"}))

    _stub(monkeypatch, '{"verdict": "ok", "reason": ')
    with pytest.raises(ValueError, match="would not decode"):
        asyncio.run(sr.review({"title": "A ticket"}))


def test_a_failure_carries_the_reply_head_so_it_can_be_diagnosed(monkeypatch):
    _stub(monkeypatch, "I ran read-only grep and sed commands to ground this.")
    with pytest.raises(ValueError, match="read-only grep"):
        asyncio.run(sr.review({"title": "A ticket"}))
